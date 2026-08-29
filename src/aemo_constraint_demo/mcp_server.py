"""MCP server exposing the AEMO NEMWeb constraint pipeline as agent tools.

Four read-only tools wrap the same pipeline the FastAPI demo uses:

  aemo_list_duid_targets       - which DUIDs/horizons this demo can answer for
  aemo_get_duid_dispatch       - recent 5-minute dispatch history for one DUID
  aemo_get_duid_constraints    - DUID -> connection point -> generic constraints, plus binding history
  aemo_forecast_duid_constraints - run the trained horizon model for one DUID

Run over stdio from the repository root:

    python run_mcp_server.py

Paths are resolved against the repository root, so the server does not depend on
the working directory the MCP client launches it from.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import joblib
import pandas as pd
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .aemo_client import (
    next_day_dispatch_paths,
    next_day_dispatch_trading_date,
    parse_multitable_zip,
    read_monthly_table_cached_first,
)
from .features import build_label_frame, parse_datetime, standardise_columns
from .forecast import forecast_from_bundle
from .scan import (
    STATUS_OK,
    filter_binding_dispatch_constraints,
    get_relevant_constraint_rows,
    resolve_duid_metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = "data/cache"
DEFAULT_MODEL_PATH = "artifacts/cathrock_6h_best_model.joblib"
SCAN_ARTIFACTS = (
    "artifacts/duid_constraint_scan_expanded_top.csv",
    "artifacts/duid_constraint_scan_expanded.csv",
    "artifacts/duid_constraint_scan_top.csv",
    "artifacts/duid_constraint_scan.csv",
)

# Slim column sets keep the parsed daily files small enough to cache in memory.
DISPATCHLOAD_COLUMNS = [
    "SETTLEMENTDATE",
    "DUID",
    "INITIALMW",
    "TOTALCLEARED",
    "AVAILABILITY",
    "SEMIDISPATCHCAP",
    "DISPATCHMODE",
    "RAMPUPRATE",
    "RAMPDOWNRATE",
]
DISPATCHCONSTRAINT_COLUMNS = [
    "SETTLEMENTDATE",
    "CONSTRAINTID",
    "RHS",
    "MARGINALVALUE",
    "VIOLATIONDEGREE",
    "INTERVENTION",
]

DataSource = Literal["auto", "cache", "network"]

SERVER_INSTRUCTIONS = """\
Tools over AEMO public NEMWeb data for DUID-level network constraint risk in the NEM.

Start with aemo_list_duid_targets: it reports which DUIDs have usable constraint labels
and which trained model bundles exist. Only DUIDs with a trained bundle can be forecast;
the demo ships one model, for CATHROCK at a 6-hour horizon.

Every tool takes a `source` argument. "cache" answers from previously downloaded NEMWeb
files only, "network" always fetches from NEMWeb (roughly 3-5 seconds per trading day),
and the default "auto" uses the cache when it already covers the requested window.

NEMWeb publishes Next Day Dispatch for the previous trading day, so nothing here is live.
The forecast is anchored at the latest published dispatch interval and reports `as_of` and
`data_lag_hours`; quote them rather than implying a real-time answer.

This is an interview/portfolio demo, not a production constraint forecaster. Report the
metrics and caveats the tools return rather than presenting the probabilities as
production-grade predictions.\
"""

mcp = MCPServer(
    name="aemo_nemweb_mcp",
    version="0.1.0",
    instructions=SERVER_INSTRUCTIONS,
)


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _repo_path(value: str | Path) -> Path:
    """Resolve a repo-relative path so the server works from any working directory."""
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def _display_path(path: Path) -> str:
    """Show a repo-relative path when possible, otherwise the absolute path."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _number(value: Any, digits: int = 4) -> float | None:
    """Return a JSON-safe rounded float, or None when the value is not numeric."""
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return round(float(number), digits)


def _integer(value: Any) -> int | None:
    """Return a JSON-safe int, or None when the value is not numeric."""
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return int(number)


def _timestamp(value: Any) -> str | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).isoformat()


def _slim(table: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if table.empty:
        return table
    table = standardise_columns(table)
    return table[[col for col in columns if col in table.columns]].copy()


@lru_cache(maxsize=8)
def _parse_dispatch_file(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse one Next Day Dispatch ZIP once per process.

    Parsing a daily file costs a few seconds, so results are cached. Callers must
    treat the returned frames as read-only.
    """
    tables = parse_multitable_zip(path, table_names=["DISPATCHLOAD", "DISPATCHCONSTRAINT"])
    return (
        _slim(tables["DISPATCHLOAD"], DISPATCHLOAD_COLUMNS),
        _slim(tables["DISPATCHCONSTRAINT"], DISPATCHCONSTRAINT_COLUMNS),
    )


def _load_dispatch_window(
    days: int,
    source: str,
    end_date: str | None = None,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> dict[str, Any]:
    """Load DISPATCHLOAD/DISPATCHCONSTRAINT for the latest N trading days."""
    paths = next_day_dispatch_paths(
        days=days,
        cache_dir=_repo_path(cache_dir),
        end_date=end_date,
        source=source,
    )
    loads: list[pd.DataFrame] = []
    constraints: list[pd.DataFrame] = []
    for path in paths:
        load, constraint = _parse_dispatch_file(str(path))
        if not load.empty:
            loads.append(load)
        if not constraint.empty:
            constraints.append(constraint)

    trading_dates = []
    for path in paths:
        date = next_day_dispatch_trading_date(path.name)
        if date is not None:
            trading_dates.append(date.date().isoformat())
    return {
        "dispatchload": pd.concat(loads, ignore_index=True) if loads else pd.DataFrame(),
        "dispatchconstraint": pd.concat(constraints, ignore_index=True) if constraints else pd.DataFrame(),
        "trading_dates": trading_dates,
        "files_used": len(paths),
    }


@lru_cache(maxsize=4)
def _standing_table(table: str, source: str, cache_dir: str) -> pd.DataFrame:
    """Load and cache a monthly MMSDM standing-data table."""
    return read_monthly_table_cached_first(table, cache_dir=cache_dir, source=source)


def _resolve_duid(duid: str, source: str, cache_dir: str | Path) -> dict[str, Any]:
    """Resolve DUID metadata and its relevant generic constraints."""
    cache = str(_repo_path(cache_dir))
    dudetail = _standing_table("DUDETAIL", source, cache)
    metadata = resolve_duid_metadata(duid, dudetail)
    if metadata["status"] != STATUS_OK:
        raise ToolError(
            f"DUID {duid.upper()} is not usable: {metadata['status']}. "
            "Call aemo_list_duid_targets to see DUIDs this demo has scanned."
        )

    spd = _standing_table("SPDCONNECTIONPOINTCONSTRAINT", source, cache)
    rows = get_relevant_constraint_rows(metadata["connection_point_id"], spd)
    genconids = sorted(rows["GENCONID"].unique().tolist()) if not rows.empty else []
    return {"metadata": metadata, "relevant_genconids": genconids}


def _duid_dispatch_rows(dispatchload: pd.DataFrame, duid: str) -> pd.DataFrame:
    if dispatchload.empty or "DUID" not in dispatchload.columns:
        return pd.DataFrame()
    rows = dispatchload[dispatchload["DUID"].astype(str).str.upper().str.strip() == duid.upper()].copy()
    if rows.empty:
        return rows
    rows["SETTLEMENTDATE"] = parse_datetime(rows["SETTLEMENTDATE"])
    rows = rows.dropna(subset=["SETTLEMENTDATE"]).sort_values("SETTLEMENTDATE")
    return rows.drop_duplicates(subset=["SETTLEMENTDATE"], keep="last").reset_index(drop=True)


def _series_summary(rows: pd.DataFrame, column: str) -> dict[str, float | None] | None:
    if column not in rows.columns:
        return None
    values = pd.to_numeric(rows[column], errors="coerce").dropna()
    if values.empty:
        return None
    return {
        "min": _number(values.min(), 2),
        "mean": _number(values.mean(), 2),
        "max": _number(values.max(), 2),
        "last": _number(values.iloc[-1], 2),
    }


def _evenly_spaced(items: list[Any], limit: int) -> list[Any]:
    """Downsample a list to at most `limit` entries, keeping the first and last."""
    if limit <= 0 or len(items) <= limit:
        return items
    last = len(items) - 1
    if limit == 1:
        return [items[last]]
    indexes = sorted({round(index * last / (limit - 1)) for index in range(limit)})
    return [items[index] for index in indexes]


def _scan_artifact_path() -> Path | None:
    for candidate in SCAN_ARTIFACTS:
        path = _repo_path(candidate)
        if path.exists():
            return path
    return None


def _trained_models() -> list[dict[str, Any]]:
    """Describe the model bundles committed under artifacts/."""
    models: list[dict[str, Any]] = []
    for path in sorted(_repo_path("artifacts").glob("*.joblib")):
        try:
            bundle = joblib.load(path)
        except Exception as exc:  # noqa: BLE001 - a stale bundle should not break discovery
            models.append({"model_path": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        metrics = bundle.get("metrics", {})
        models.append(
            {
                "model_path": _display_path(path),
                "duid": bundle.get("duid"),
                "horizon_hours": bundle.get("horizon_hours", metrics.get("horizon_hours")),
                "model_type": metrics.get("model_type"),
                "feature_set": bundle.get("feature_set"),
                "target_column": bundle.get("target_column"),
                "trained_at_utc": bundle.get("trained_at_utc"),
            }
        )
    return models


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


@mcp.tool(
    name="aemo_list_duid_targets",
    annotations=ToolAnnotations(
        title="List scanned AEMO DUID targets",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def aemo_list_duid_targets(
    horizon_hours: Annotated[
        int | None,
        Field(default=None, description="Only return rows for this forecast horizon, e.g. 1, 3 or 6.", ge=1, le=72),
    ] = None,
    recommended_only: Annotated[
        bool,
        Field(default=False, description="Only return DUID/window rows the Phase 1.5 scan marked as recommended targets."),
    ] = False,
    limit: Annotated[int, Field(default=10, description="Maximum target rows to return.", ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """Which DUIDs this demo can answer for, ranked by constraint-label quality.

    Reads the committed target-discovery scan (no network access). Each row reports how
    often a DUID's relevant generic constraints actually bound over a historical window,
    which is what makes a DUID trainable. Also lists the trained model bundles available
    to aemo_forecast_duid_constraints.

    Use this first when you do not already know which DUID to query.

    Returns a dict with:
      scan_file (str), total_rows (int), returned (int),
      targets: [{duid, days, horizon_hours, connection_point_id, relevant_genconid_count,
                 total_intervals, positive_intervals, positive_rate, recommended,
                 recommendation_reason}],
      trained_models: [{model_path, duid, horizon_hours, model_type, feature_set}]
    """
    scan_path = _scan_artifact_path()
    if scan_path is None:
        raise ToolError(
            "No target-discovery scan artifact found. Generate one with "
            "`python scripts/find_constrained_duids.py --days 60 --horizons-hours 1 3 6 "
            "--out artifacts/duid_constraint_scan_expanded.csv`."
        )

    scan = pd.read_csv(scan_path)
    total_rows = len(scan)
    if "status" in scan.columns:
        scan = scan[scan["status"].astype(str) == STATUS_OK]
    if horizon_hours is not None and "horizon_hours" in scan.columns:
        scan = scan[pd.to_numeric(scan["horizon_hours"], errors="coerce") == horizon_hours]
    if recommended_only and "recommended" in scan.columns:
        scan = scan[scan["recommended"].astype(str).str.lower() == "true"]
    if "positive_intervals" in scan.columns:
        scan = scan.sort_values("positive_intervals", ascending=False)

    targets = []
    for _, row in scan.head(limit).iterrows():
        targets.append(
            {
                "duid": str(row.get("duid", "")).upper(),
                "days": _integer(row.get("days")),
                "horizon_hours": _integer(row.get("horizon_hours")),
                "connection_point_id": str(row.get("connection_point_id", "") or ""),
                "relevant_genconid_count": _integer(row.get("relevant_genconid_count")),
                "total_intervals": _integer(row.get("total_intervals")),
                "positive_intervals": _integer(row.get("positive_intervals")),
                "positive_rate": _number(row.get("positive_rate"), 6),
                "last_positive_dispatch_interval": _timestamp(row.get("last_positive_dispatch_interval")),
                "recommended": str(row.get("recommended", "")).lower() == "true",
                "recommendation_reason": str(row.get("recommendation_reason", "") or ""),
            }
        )

    return {
        "scan_file": _display_path(scan_path),
        "total_rows": total_rows,
        "matched_rows": len(scan),
        "returned": len(targets),
        "targets": targets,
        "trained_models": _trained_models(),
        "notes": (
            "positive_rate is the share of dispatch intervals where a relevant generic constraint "
            "bound. Only DUIDs listed in trained_models can be forecast without retraining."
        ),
    }


@mcp.tool(
    name="aemo_get_duid_dispatch",
    annotations=ToolAnnotations(
        title="Get AEMO dispatch history for a DUID",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
def aemo_get_duid_dispatch(
    duid: Annotated[str, Field(description="AEMO Dispatchable Unit ID, e.g. 'CATHROCK'.", min_length=1, max_length=32)],
    days: Annotated[int, Field(default=2, description="Number of recent trading days to load.", ge=1, le=14)] = 2,
    source: Annotated[
        DataSource,
        Field(default="auto", description="'cache' for local files only, 'network' to fetch from NEMWeb, 'auto' to prefer the cache."),
    ] = "auto",
    end_date: Annotated[
        str | None,
        Field(default=None, description="Only use trading days on or before this date, e.g. '2026-05-18'."),
    ] = None,
    max_intervals: Annotated[
        int,
        Field(default=24, description="Maximum 5-minute interval rows to return; the window is downsampled evenly beyond this.", ge=1, le=288),
    ] = 24,
) -> dict[str, Any]:
    """Recent 5-minute dispatch outcomes for one DUID from NEMWeb Next Day Dispatch files.

    Returns the unit's cleared energy target, initial MW, and availability per dispatch
    interval, plus min/mean/max/last summaries over the window. Loading a trading day
    from NEMWeb takes a few seconds; already-parsed days are reused within the session.

    Returns a dict with:
      duid, data_source, trading_dates, interval_count, first_interval, last_interval,
      summary: {totalcleared_mw, initialmw, availability_mw} each {min, mean, max, last},
      intervals: [{settlementdate, totalcleared_mw, initialmw, availability_mw, semidispatchcap}]
    """
    window = _load_dispatch_window(days=days, source=source, end_date=end_date)
    rows = _duid_dispatch_rows(window["dispatchload"], duid)
    if rows.empty:
        raise ToolError(
            f"No DISPATCHLOAD rows for DUID {duid.upper()} in the last {days} trading day(s) "
            f"({', '.join(window['trading_dates']) or 'no files'}). Check the DUID spelling, "
            "widen `days`, or call aemo_list_duid_targets for known DUIDs."
        )

    intervals = [
        {
            "settlementdate": _timestamp(row["SETTLEMENTDATE"]),
            "totalcleared_mw": _number(row.get("TOTALCLEARED"), 2),
            "initialmw": _number(row.get("INITIALMW"), 2),
            "availability_mw": _number(row.get("AVAILABILITY"), 2),
            "semidispatchcap": _integer(row.get("SEMIDISPATCHCAP")),
        }
        for _, row in rows.iterrows()
    ]

    sampled = _evenly_spaced(intervals, max_intervals)
    return {
        "duid": duid.upper(),
        "data_source": source,
        "trading_dates": window["trading_dates"],
        "interval_count": len(rows),
        "first_interval": _timestamp(rows["SETTLEMENTDATE"].iloc[0]),
        "last_interval": _timestamp(rows["SETTLEMENTDATE"].iloc[-1]),
        "summary": {
            "totalcleared_mw": _series_summary(rows, "TOTALCLEARED"),
            "initialmw": _series_summary(rows, "INITIALMW"),
            "availability_mw": _series_summary(rows, "AVAILABILITY"),
        },
        "returned_intervals": len(sampled),
        "intervals": sampled,
        "notes": (
            "Source: NEMWeb Reports/CURRENT/Next_Day_Dispatch. SEMIDISPATCHCAP=1 means the "
            "semi-scheduled unit was capped by a binding constraint at that interval."
        ),
    }


@mcp.tool(
    name="aemo_get_duid_constraints",
    annotations=ToolAnnotations(
        title="Get AEMO network constraints for a DUID",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    ),
)
def aemo_get_duid_constraints(
    duid: Annotated[str, Field(description="AEMO Dispatchable Unit ID, e.g. 'CATHROCK'.", min_length=1, max_length=32)],
    days: Annotated[int, Field(default=2, description="Number of recent trading days of binding history to summarise.", ge=1, le=14)] = 2,
    source: Annotated[
        DataSource,
        Field(default="auto", description="'cache' for local files only, 'network' to fetch from NEMWeb, 'auto' to prefer the cache."),
    ] = "auto",
    end_date: Annotated[
        str | None,
        Field(default=None, description="Only use trading days on or before this date, e.g. '2026-05-18'."),
    ] = None,
    include_binding_history: Annotated[
        bool,
        Field(default=True, description="Set false to return the constraint mapping only and skip loading dispatch files."),
    ] = True,
    top_n: Annotated[int, Field(default=10, description="How many of the most frequently binding constraints to return.", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Generic constraints relevant to a DUID, and which of them actually bound recently.

    Maps DUID -> CONNECTIONPOINTID via DUDETAIL, then CONNECTIONPOINTID -> GENCONID via
    SPDCONNECTIONPOINTCONSTRAINT (non-FCAS LHS terms preferred). With
    include_binding_history, also summarises DISPATCHCONSTRAINT over recent trading days:
    a constraint counts as binding when MARGINALVALUE > 0 or VIOLATIONDEGREE > 0.

    Returns a dict with:
      duid, connection_point_id, relevant_genconid_count,
      relevant_genconids (up to 50), binding_history: {trading_dates, intervals_total,
      intervals_constrained, constrained_rate, binding_constraint_count,
      top_binding_constraints: [{genconid, binding_intervals, max_marginal_value,
      max_violation_degree, last_binding_interval}]}
    """
    resolved = _resolve_duid(duid, source=source, cache_dir=DEFAULT_CACHE_DIR)
    metadata = resolved["metadata"]
    genconids = resolved["relevant_genconids"]
    if not genconids:
        raise ToolError(
            f"DUID {duid.upper()} maps to connection point {metadata['connection_point_id']} but no "
            "generic constraints reference it on the LHS, so there is nothing to forecast."
        )

    result: dict[str, Any] = {
        "duid": metadata["duid"],
        "connection_point_id": metadata["connection_point_id"],
        "relevant_genconid_count": len(genconids),
        "relevant_genconids": genconids[:50],
        "mapping_source": "DUDETAIL -> SPDCONNECTIONPOINTCONSTRAINT (monthly MMSDM standing data)",
        "binding_definition": "MARGINALVALUE > 0 or VIOLATIONDEGREE > 0 in DISPATCHCONSTRAINT",
    }
    if not include_binding_history:
        result["binding_history"] = None
        return result

    window = _load_dispatch_window(days=days, source=source, end_date=end_date)
    labels = build_label_frame(
        dispatchload=window["dispatchload"],
        dispatchconstraint=window["dispatchconstraint"],
        duid=duid,
        relevant_genconids=genconids,
    )
    binding = filter_binding_dispatch_constraints(window["dispatchconstraint"])
    if not binding.empty:
        binding = binding[binding["CONSTRAINTID"].astype(str).str.upper().isin(set(genconids))].copy()

    top_constraints: list[dict[str, Any]] = []
    if not binding.empty:
        binding["SETTLEMENTDATE"] = parse_datetime(binding["SETTLEMENTDATE"])
        binding["MARGINALVALUE"] = pd.to_numeric(binding.get("MARGINALVALUE", 0), errors="coerce").fillna(0)
        binding["VIOLATIONDEGREE"] = pd.to_numeric(binding.get("VIOLATIONDEGREE", 0), errors="coerce").fillna(0)
        grouped = (
            binding.groupby(binding["CONSTRAINTID"].astype(str).str.upper())
            .agg(
                binding_intervals=("SETTLEMENTDATE", "nunique"),
                max_marginal_value=("MARGINALVALUE", "max"),
                max_violation_degree=("VIOLATIONDEGREE", "max"),
                last_binding_interval=("SETTLEMENTDATE", "max"),
            )
            .sort_values("binding_intervals", ascending=False)
            .head(top_n)
        )
        top_constraints = [
            {
                "genconid": str(genconid),
                "binding_intervals": int(row["binding_intervals"]),
                "max_marginal_value": _number(row["max_marginal_value"], 2),
                "max_violation_degree": _number(row["max_violation_degree"], 4),
                "last_binding_interval": _timestamp(row["last_binding_interval"]),
            }
            for genconid, row in grouped.iterrows()
        ]

    constrained = int(labels["target_constrained"].sum())
    result["binding_history"] = {
        "trading_dates": window["trading_dates"],
        "data_source": source,
        "intervals_total": int(len(labels)),
        "intervals_constrained": constrained,
        "constrained_rate": _number(constrained / len(labels) if len(labels) else 0, 6),
        "binding_constraint_count": int(len(top_constraints)),
        "top_binding_constraints": top_constraints,
    }
    return result


@mcp.tool(
    name="aemo_forecast_duid_constraints",
    annotations=ToolAnnotations(
        title="Forecast constraint risk for a DUID",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    ),
)
def aemo_forecast_duid_constraints(
    duid: Annotated[str, Field(description="AEMO Dispatchable Unit ID the model was trained for, e.g. 'CATHROCK'.", min_length=1, max_length=32)],
    model_path: Annotated[
        str,
        Field(default=DEFAULT_MODEL_PATH, description="Model bundle to query, relative to the repo root. See aemo_list_duid_targets for available bundles."),
    ] = DEFAULT_MODEL_PATH,
    horizon_hours: Annotated[
        int | None,
        Field(default=None, description="Forecast horizon; omit to use the model bundle's own horizon. A mismatch is rejected.", ge=1, le=72),
    ] = None,
    history_days: Annotated[
        int,
        Field(default=2, description="Trading days of dispatch history to load. The model's lag features need at least the horizon's worth.", ge=1, le=14),
    ] = 2,
    source: Annotated[
        DataSource,
        Field(default="auto", description="'cache' for local files only, 'network' to fetch from NEMWeb, 'auto' to prefer the cache."),
    ] = "auto",
    end_date: Annotated[
        str | None,
        Field(default=None, description="Only use trading days on or before this date, e.g. '2026-04-22'. Use it to replay a historical origin."),
    ] = None,
    use_weather: Annotated[
        bool,
        Field(default=True, description="Fetch Open-Meteo weather for the history window. Set false for a fully offline run; weather features are then imputed."),
    ] = True,
) -> dict[str, Any]:
    """Estimate the probability that a network constraint binds for a DUID in the next horizon.

    The model is origin-anchored: it scores the state at one dispatch interval and
    estimates whether a relevant generic constraint binds over the following
    horizon_hours. One call therefore returns one probability, anchored at the latest
    dispatch interval NEMWeb has published - check `as_of` and `data_lag_hours`, because
    Next Day Dispatch publishes the previous trading day, not live data.

    The model is trained per DUID, so the requested DUID must match the bundle.

    Returns a dict with:
      duid, connection_point, horizon_hours, as_of, horizon_end, data_lag_hours,
      constraint_risk {probability, risk_level, statement},
      feature_coverage {required, observed, imputed, imputed_examples},
      dispatch_source {source, history_days, first_interval, last_interval, interval_count},
      weather_source, weather_warning, model {model_type, feature_set, target_column,
      positive_rate, trained_at_utc}, planned_constraints: [{genconid, active_intervals,
      first_interval, last_interval}], recent_risk_history: [{origin, probability,
      risk_level}], relevant_genconid_count, limitations

    A non-zero feature_coverage.imputed means the model was scored on incomplete inputs -
    usually too little dispatch history, or weather disabled. Say so when reporting the
    probability.
    """
    path = _repo_path(model_path)
    if not path.exists():
        available = ", ".join(model["model_path"] for model in _trained_models()) or "none"
        raise ToolError(
            f"Model bundle not found: {model_path}. Available bundles: {available}. "
            "Train one with `python -m src.aemo_constraint_demo.train --duid <DUID> --days 60 "
            "--horizon-hours 6 --model-out artifacts/<duid>.joblib`."
        )

    try:
        payload = forecast_from_bundle(
            path,
            duid=duid,
            horizon_hours=horizon_hours,
            history_days=history_days,
            source=source,
            end_date=end_date,
            use_weather=use_weather,
            cache_dir=_repo_path(DEFAULT_CACHE_DIR),
        )
    except ValueError as exc:  # DUID/horizon/history problems carry a usable message
        raise ToolError(str(exc)) from exc

    return {
        "duid": payload["duid"],
        "connection_point": payload["connection_point"],
        "horizon_hours": payload["horizon_hours"],
        "as_of": payload["as_of"],
        "horizon_end": payload["horizon_end"],
        "data_lag_hours": payload["data_lag_hours"],
        "generated_at": payload["generated_at"],
        "constraint_risk": payload["constraint_risk"],
        "feature_coverage": payload["feature_coverage"],
        "dispatch_source": payload["dispatch_source"],
        "weather_source": payload["weather_source"],
        "weather_warning": payload["weather_warning"],
        "model": payload["model"],
        "model_path": _display_path(path),
        "relevant_genconid_count": payload["relevant_genconids_count"],
        "planned_constraints": payload["planned_constraints"],
        "planned_constraints_source": payload["planned_constraints_source"],
        "recent_risk_history": payload["recent_risk_history"],
        "limitations": payload["limitations"],
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
