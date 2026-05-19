"""Target discovery scans for DUID constraint-label statistics."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .aemo_client import load_next_day_dispatch_tables, read_monthly_table
from .features import build_label_frame, parse_datetime, standardise_columns

SCAN_OUTPUT_COLUMNS = [
    "duid",
    "days",
    "horizon_hours",
    "status",
    "regionid",
    "stationid",
    "station_name",
    "connection_point_id",
    "relevant_genconid_count",
    "total_intervals",
    "positive_intervals",
    "positive_rate",
    "unique_binding_genconid_count",
    "first_positive_dispatch_interval",
    "last_positive_dispatch_interval",
    "recommended",
    "recommendation_reason",
]

STATUS_OK = "ok"
STATUS_MISSING_DUID = "missing_duid"
STATUS_MISSING_CONNECTION_POINT = "missing_connection_point"
STATUS_NO_RELEVANT_GENCONIDS = "no_relevant_genconids"
STATUS_NO_DISPATCH_DATA = "no_dispatch_data"
STATUS_DOWNLOAD_FAILED = "download_failed"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_ERROR = "error"

FCAS_BIDTYPES = {
    "RAISE6SEC",
    "RAISE60SEC",
    "RAISE5MIN",
    "LOWER6SEC",
    "LOWER60SEC",
    "LOWER5MIN",
    "RAISEREG",
    "LOWERREG",
}


@dataclass(frozen=True)
class ScanThresholds:
    min_positive_samples: int = 20
    min_positive_rate: float = 0.005
    max_positive_rate: float = 0.10


def load_standing_data(
    static_year: int | None = None,
    static_month: int | None = None,
    cache_dir: str | Path = "data/cache",
) -> dict[str, pd.DataFrame]:
    """Load the standing-data tables needed for DUID target discovery."""
    return {
        "DUDETAIL": read_monthly_table("DUDETAIL", year=static_year, month=static_month, cache_dir=cache_dir),
        "SPDCONNECTIONPOINTCONSTRAINT": read_monthly_table(
            "SPDCONNECTIONPOINTCONSTRAINT",
            year=static_year,
            month=static_month,
            cache_dir=cache_dir,
        ),
    }


def load_dispatch_tables_for_window(
    days: int,
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Load dispatch load and constraint rows for a recent rolling window."""
    return load_next_day_dispatch_tables(
        days=days,
        table_names=["DISPATCHLOAD", "DISPATCHCONSTRAINT"],
        cache_dir=cache_dir,
        end_date=end_date,
    )


def load_dispatch_constraints_for_window(
    days: int,
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load only DISPATCHCONSTRAINT rows for a recent rolling window."""
    return load_dispatch_tables_for_window(days=days, cache_dir=cache_dir, end_date=end_date)["DISPATCHCONSTRAINT"]


def filter_binding_dispatch_constraints(dispatchconstraint: pd.DataFrame, bind_threshold: float = 0.0) -> pd.DataFrame:
    """Return only rows that can contribute positive binding labels."""
    con = standardise_columns(dispatchconstraint)
    if con.empty or "CONSTRAINTID" not in con.columns:
        return con

    if "INTERVENTION" in con.columns:
        con = con[con["INTERVENTION"].astype(str).str.strip().isin(["0", "", "nan", "None"])].copy()

    marginal = (
        pd.to_numeric(con["MARGINALVALUE"], errors="coerce").fillna(0)
        if "MARGINALVALUE" in con.columns
        else pd.Series(0, index=con.index)
    )
    violation = (
        pd.to_numeric(con["VIOLATIONDEGREE"], errors="coerce").fillna(0)
        if "VIOLATIONDEGREE" in con.columns
        else pd.Series(0, index=con.index)
    )
    return con[(marginal > bind_threshold) | (violation > 0)].copy()


def filter_dispatch_table_to_recent_days(
    table: pd.DataFrame,
    days: int,
    time_col: str = "SETTLEMENTDATE",
) -> pd.DataFrame:
    """Filter a dispatch table to the trailing day window using its latest timestamp."""
    if table.empty:
        return table.copy()

    column_lookup = {str(col).strip().upper(): col for col in table.columns}
    source_col = column_lookup.get(time_col.upper())
    if source_col is None:
        return table.copy()

    timestamps = parse_datetime(table[source_col])
    latest = timestamps.max()
    if pd.isna(latest):
        return table.copy()

    start = latest - pd.Timedelta(days=int(days))
    return table[timestamps > start].copy()


def filter_dispatchload_to_duids(
    dispatchload: pd.DataFrame,
    duids: Iterable[str],
) -> pd.DataFrame:
    """Keep only candidate DUID rows from DISPATCHLOAD before repeated scans."""
    load = standardise_columns(dispatchload)
    if load.empty or "DUID" not in load.columns:
        return load

    wanted = {str(duid).upper().strip() for duid in duids}
    return load[load["DUID"].astype(str).str.upper().str.strip().isin(wanted)].copy()


def split_dispatchload_by_duid(
    dispatchload: pd.DataFrame,
    duids: Iterable[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Pre-split DISPATCHLOAD rows by DUID for repeated scan summaries."""
    load = standardise_columns(dispatchload)
    if load.empty or "DUID" not in load.columns:
        return {}

    load = load.copy()
    load["DUID"] = load["DUID"].astype(str).str.upper().str.strip()
    if duids is not None:
        wanted = {str(duid).upper().strip() for duid in duids}
        load = load[load["DUID"].isin(wanted)].copy()

    return {duid: rows.copy() for duid, rows in load.groupby("DUID", sort=False)}


def _first_present(row: pd.Series, columns: Sequence[str]) -> str:
    for col in columns:
        if col in row.index and pd.notna(row[col]) and str(row[col]).strip():
            return str(row[col]).strip()
    return ""


def _current_rows_by_duid(dudetail: pd.DataFrame) -> pd.DataFrame:
    detail = standardise_columns(dudetail)
    if detail.empty or "DUID" not in detail.columns:
        return pd.DataFrame(columns=detail.columns)

    detail = detail.copy()
    detail["DUID"] = detail["DUID"].astype(str).str.upper().str.strip()
    if "EFFECTIVEDATE" in detail.columns:
        detail["_EFFECTIVEDATE"] = parse_datetime(detail["EFFECTIVEDATE"])
    else:
        detail["_EFFECTIVEDATE"] = pd.Timestamp.min
    if "VERSIONNO" in detail.columns:
        detail["_VERSIONNO"] = pd.to_numeric(detail["VERSIONNO"], errors="coerce").fillna(-1)
    else:
        detail["_VERSIONNO"] = -1

    return (
        detail.sort_values(["DUID", "_EFFECTIVEDATE", "_VERSIONNO"])
        .groupby("DUID", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def resolve_duid_metadata(duid: str, dudetail: pd.DataFrame) -> dict:
    """Resolve the latest available metadata row for a DUID."""
    duid_upper = str(duid).strip().upper()
    rows = _current_rows_by_duid(dudetail)
    if rows.empty or "DUID" not in rows.columns:
        return _metadata_result(duid_upper, STATUS_MISSING_DUID)

    match = rows[rows["DUID"].astype(str).str.upper() == duid_upper]
    if match.empty:
        return _metadata_result(duid_upper, STATUS_MISSING_DUID)

    row = match.iloc[0]
    connection_point = _first_present(row, ["CONNECTIONPOINTID", "CONNECTION_POINT_ID", "CONNECTIONPOINT"]).upper()
    status = STATUS_OK if connection_point else STATUS_MISSING_CONNECTION_POINT
    return {
        "duid": duid_upper,
        "status": status,
        "regionid": _first_present(row, ["REGIONID", "REGION_ID", "REGION"]).upper(),
        "stationid": _first_present(row, ["STATIONID", "STATION_ID"]).upper(),
        "station_name": _first_present(row, ["STATIONNAME", "STATION_NAME", "STATION"]),
        "connection_point_id": connection_point,
    }


def _metadata_result(duid: str, status: str) -> dict:
    return {
        "duid": duid,
        "status": status,
        "regionid": "",
        "stationid": "",
        "station_name": "",
        "connection_point_id": "",
    }


def get_connection_point_for_duid(duid: str, dudetail: pd.DataFrame) -> str | None:
    """Return the current connection point for a DUID, or None if unavailable."""
    metadata = resolve_duid_metadata(duid, dudetail)
    return metadata["connection_point_id"] or None


def get_relevant_genconids(
    connection_point_id: str,
    spd_connectionpoint: pd.DataFrame,
    prefer_energy_terms: bool = True,
) -> set[str]:
    """Return relevant GENCONIDs for a connection point."""
    rows = get_relevant_constraint_rows(connection_point_id, spd_connectionpoint, prefer_energy_terms=prefer_energy_terms)
    return set(rows["GENCONID"].dropna().astype(str).str.upper().unique())


def get_relevant_constraint_rows(
    connection_point_id: str,
    spd_connectionpoint: pd.DataFrame,
    prefer_energy_terms: bool = True,
) -> pd.DataFrame:
    """Return filtered SPDCONNECTIONPOINTCONSTRAINT rows for a connection point."""
    spd = standardise_columns(spd_connectionpoint)
    required = {"CONNECTIONPOINTID", "GENCONID"}
    missing = required - set(spd.columns)
    if missing:
        raise ValueError(f"SPDCONNECTIONPOINTCONSTRAINT is missing columns: {sorted(missing)}")

    connection_point = str(connection_point_id).strip().upper()
    rows = spd[spd["CONNECTIONPOINTID"].astype(str).str.upper().str.strip() == connection_point].copy()
    if rows.empty:
        return rows

    if "FACTOR" in rows.columns:
        rows["_FACTOR"] = pd.to_numeric(rows["FACTOR"], errors="coerce").fillna(0)
        rows = rows[rows["_FACTOR"].abs() > 0]

    if prefer_energy_terms and "BIDTYPE" in rows.columns:
        bidtype = rows["BIDTYPE"].fillna("").astype(str).str.upper().str.strip()
        non_fcas = rows[~bidtype.isin(FCAS_BIDTYPES)]
        if not non_fcas.empty:
            rows = non_fcas

    rows = rows.dropna(subset=["GENCONID"]).copy()
    if not rows.empty:
        rows["GENCONID"] = rows["GENCONID"].astype(str).str.upper().str.strip()
    return rows.reset_index(drop=True)


def build_constraint_labels_for_duid(
    duid: str,
    days: int,
    dudetail: pd.DataFrame,
    spd_connectionpoint: pd.DataFrame,
    dispatchload: pd.DataFrame | None = None,
    dispatchconstraint: pd.DataFrame | None = None,
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
    bind_threshold: float = 0.0,
) -> pd.DataFrame:
    """Build interval labels for a DUID/window using the shared feature helper."""
    metadata = resolve_duid_metadata(duid, dudetail)
    if metadata["status"] != STATUS_OK:
        raise ValueError(metadata["status"])

    relevant = get_relevant_genconids(metadata["connection_point_id"], spd_connectionpoint)
    if not relevant:
        raise ValueError(STATUS_NO_RELEVANT_GENCONIDS)

    if dispatchload is None or dispatchconstraint is None:
        dispatch = load_dispatch_tables_for_window(days=days, cache_dir=cache_dir, end_date=end_date)
        dispatchload = dispatch["DISPATCHLOAD"]
        dispatchconstraint = dispatch["DISPATCHCONSTRAINT"]

    return build_label_frame(
        dispatchload=dispatchload,
        dispatchconstraint=dispatchconstraint,
        duid=duid,
        relevant_genconids=relevant,
        bind_threshold=bind_threshold,
    )


def horizon_label_column(horizon_hours: int) -> str:
    """Return the conventional target column name for a forecast horizon."""
    horizon = int(horizon_hours)
    if horizon <= 0:
        raise ValueError("horizon_hours must be positive for a forecast-horizon label.")
    return f"target_constrained_next_{horizon}h"


def add_horizon_labels(
    labels: pd.DataFrame,
    horizons_hours: Sequence[int],
    intervals_per_hour: int = 12,
) -> pd.DataFrame:
    """Add future-only horizon labels derived from ``target_constrained``.

    For each interval t, the next-Xh label is 1 when any current-interval
    binding label is 1 in t+1 through t+X. The current interval is excluded.
    """
    if intervals_per_hour <= 0:
        raise ValueError("intervals_per_hour must be positive.")

    out = labels.copy()
    if "SETTLEMENTDATE" in out.columns:
        out["SETTLEMENTDATE"] = parse_datetime(out["SETTLEMENTDATE"])
        out = out.sort_values("SETTLEMENTDATE").reset_index(drop=True)
    if "target_constrained" not in out.columns:
        raise ValueError("labels must contain target_constrained.")

    current = out["target_constrained"].fillna(0).astype(int)
    for horizon in _normalise_horizons(horizons_hours):
        if horizon <= 0:
            continue
        steps = horizon * intervals_per_hour
        future_any = (
            current.iloc[::-1]
            .shift(1)
            .rolling(window=steps, min_periods=1)
            .max()
            .iloc[::-1]
            .fillna(0)
            .astype(int)
        )
        out[horizon_label_column(horizon)] = future_any
    return out


def summarise_duid_window(
    duid: str,
    days: int,
    thresholds: ScanThresholds,
    dudetail: pd.DataFrame,
    spd_connectionpoint: pd.DataFrame,
    dispatchload: pd.DataFrame,
    dispatchconstraint: pd.DataFrame,
    bind_threshold: float = 0.0,
    horizon_hours: int = 0,
    horizon_thresholds: ScanThresholds | None = None,
) -> dict:
    """Summarise constraint-label statistics for one DUID/window."""
    return summarise_duid_window_horizons(
        duid=duid,
        days=days,
        thresholds=thresholds,
        dudetail=dudetail,
        spd_connectionpoint=spd_connectionpoint,
        dispatchload=dispatchload,
        dispatchconstraint=dispatchconstraint,
        bind_threshold=bind_threshold,
        horizons_hours=[horizon_hours],
        horizon_thresholds=horizon_thresholds,
    )[0]


def summarise_duid_window_horizons(
    duid: str,
    days: int,
    thresholds: ScanThresholds,
    dudetail: pd.DataFrame,
    spd_connectionpoint: pd.DataFrame,
    dispatchload: pd.DataFrame,
    dispatchconstraint: pd.DataFrame,
    bind_threshold: float = 0.0,
    horizons_hours: Sequence[int] | None = None,
    horizon_thresholds: ScanThresholds | None = None,
) -> list[dict]:
    """Summarise constraint-label statistics for one DUID/window/horizon set."""
    horizons = _normalise_horizons(horizons_hours or [0])
    metadata = resolve_duid_metadata(duid, dudetail)
    if metadata["status"] != STATUS_OK:
        return _empty_results_for_horizons(
            duid=metadata["duid"],
            days=days,
            metadata=metadata,
            status=metadata["status"],
            horizons_hours=horizons,
            thresholds=thresholds,
            horizon_thresholds=horizon_thresholds,
        )

    relevant_rows = get_relevant_constraint_rows(metadata["connection_point_id"], spd_connectionpoint)
    relevant = sorted(relevant_rows["GENCONID"].dropna().astype(str).str.upper().unique().tolist())
    if not relevant:
        return _empty_results_for_horizons(
            duid=metadata["duid"],
            days=days,
            metadata=metadata,
            status=STATUS_NO_RELEVANT_GENCONIDS,
            horizons_hours=horizons,
            thresholds=thresholds,
            horizon_thresholds=horizon_thresholds,
        )

    try:
        labels = build_label_frame(
            dispatchload=dispatchload,
            dispatchconstraint=dispatchconstraint,
            duid=metadata["duid"],
            relevant_genconids=relevant,
            bind_threshold=bind_threshold,
        )
    except ValueError as exc:
        if "No DISPATCHLOAD rows" in str(exc) or "DISPATCHLOAD is empty" in str(exc):
            return _empty_results_for_horizons(
                duid=metadata["duid"],
                days=days,
                metadata=metadata,
                status=STATUS_NO_DISPATCH_DATA,
                horizons_hours=horizons,
                thresholds=thresholds,
                horizon_thresholds=horizon_thresholds,
            )
        return _empty_results_for_horizons(
            duid=metadata["duid"],
            days=days,
            metadata=metadata,
            status=STATUS_ERROR,
            horizons_hours=horizons,
            thresholds=thresholds,
            horizon_thresholds=horizon_thresholds,
            reason=str(exc),
        )

    horizon_values = [horizon for horizon in horizons if horizon > 0]
    if horizon_values:
        labels = add_horizon_labels(labels, horizon_values)

    rows: list[dict] = []
    for horizon in horizons:
        row = _empty_result(
            duid=metadata["duid"],
            days=days,
            metadata=metadata,
            status=STATUS_OK,
            horizon_hours=horizon,
        )
        row["relevant_genconid_count"] = len(relevant)
        label_col = horizon_label_column(horizon) if horizon > 0 else "target_constrained"
        row.update(_summarise_label_frame(labels, label_col=label_col))
        row["status"] = STATUS_OK if row["total_intervals"] > 0 else STATUS_NO_DISPATCH_DATA
        rows.append(_with_recommendation(row, _thresholds_for_horizon(horizon, thresholds, horizon_thresholds)))
    return rows


def _empty_result(
    duid: str,
    days: int,
    metadata: dict | None = None,
    status: str = STATUS_ERROR,
    horizon_hours: int = 0,
) -> dict:
    metadata = metadata or _metadata_result(str(duid).upper(), status)
    return {
        "duid": str(duid).upper(),
        "days": int(days),
        "horizon_hours": int(horizon_hours),
        "status": status,
        "regionid": metadata.get("regionid", ""),
        "stationid": metadata.get("stationid", ""),
        "station_name": metadata.get("station_name", ""),
        "connection_point_id": metadata.get("connection_point_id", ""),
        "relevant_genconid_count": 0,
        "total_intervals": 0,
        "positive_intervals": 0,
        "positive_rate": 0.0,
        "unique_binding_genconid_count": 0,
        "first_positive_dispatch_interval": "",
        "last_positive_dispatch_interval": "",
        "recommended": False,
        "recommendation_reason": "",
    }


def failure_result(
    duid: str,
    days: int,
    status: str,
    reason: str,
    dudetail: pd.DataFrame | None = None,
    horizon_hours: int = 0,
) -> dict:
    """Build a standards-compliant failure row."""
    metadata = resolve_duid_metadata(duid, dudetail) if dudetail is not None else None
    result = _empty_result(
        duid=str(duid).upper(),
        days=days,
        metadata=metadata,
        status=status,
        horizon_hours=horizon_hours,
    )
    result["recommendation_reason"] = reason
    return result


def _empty_results_for_horizons(
    duid: str,
    days: int,
    metadata: dict | None,
    status: str,
    horizons_hours: Sequence[int],
    thresholds: ScanThresholds,
    horizon_thresholds: ScanThresholds | None = None,
    reason: str = "",
) -> list[dict]:
    rows = []
    for horizon in horizons_hours:
        row = _empty_result(duid=duid, days=days, metadata=metadata, status=status, horizon_hours=horizon)
        if reason:
            row["recommendation_reason"] = reason
        rows.append(_with_recommendation(row, _thresholds_for_horizon(horizon, thresholds, horizon_thresholds)))
    return rows


def _summarise_label_frame(labels: pd.DataFrame, label_col: str = "target_constrained") -> dict:
    frame = labels.copy()
    if label_col not in frame.columns:
        raise ValueError(f"labels must contain {label_col}.")
    total_intervals = int(len(frame))
    if total_intervals == 0:
        return {
            "total_intervals": 0,
            "positive_intervals": 0,
            "positive_rate": 0.0,
            "unique_binding_genconid_count": 0,
            "first_positive_dispatch_interval": "",
            "last_positive_dispatch_interval": "",
        }

    positives = frame[frame[label_col].astype(int) == 1].copy()
    positive_intervals = int(len(positives))
    event_positives = frame[frame["target_constrained"].astype(int) == 1].copy()
    binding_ids = _unique_binding_ids(event_positives.get("binding_constraint_ids", pd.Series(dtype=str)))
    if positives.empty:
        first_positive = ""
        last_positive = ""
    else:
        positive_times = parse_datetime(positives["SETTLEMENTDATE"]).dropna()
        first_positive = _format_timestamp(positive_times.min()) if not positive_times.empty else ""
        last_positive = _format_timestamp(positive_times.max()) if not positive_times.empty else ""

    return {
        "total_intervals": total_intervals,
        "positive_intervals": positive_intervals,
        "positive_rate": positive_intervals / total_intervals,
        "unique_binding_genconid_count": len(binding_ids),
        "first_positive_dispatch_interval": first_positive,
        "last_positive_dispatch_interval": last_positive,
    }


def _unique_binding_ids(values: Iterable) -> set[str]:
    unique: set[str] = set()
    for value in values:
        for item in str(value).split(";"):
            item = item.strip().upper()
            if item:
                unique.add(item)
    return unique


def _format_timestamp(value: pd.Timestamp) -> str:
    if pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _with_recommendation(result: dict, thresholds: ScanThresholds) -> dict:
    recommended, reason = recommend_candidate(
        status=result["status"],
        relevant_genconid_count=int(result["relevant_genconid_count"]),
        positive_intervals=int(result["positive_intervals"]),
        positive_rate=float(result["positive_rate"]),
        thresholds=thresholds,
        horizon_hours=int(result.get("horizon_hours", 0)),
    )
    result["recommended"] = recommended
    if not result.get("recommendation_reason"):
        result["recommendation_reason"] = reason
    return result


def recommend_candidate(
    status: str,
    relevant_genconid_count: int,
    positive_intervals: int,
    positive_rate: float,
    thresholds: ScanThresholds,
    horizon_hours: int = 0,
) -> tuple[bool, str]:
    """Apply current-interval or horizon-label recommendation criteria."""
    if status != STATUS_OK:
        return False, f"status={status}"
    if relevant_genconid_count <= 0:
        return False, "no relevant GENCONIDs"
    if positive_intervals < thresholds.min_positive_samples:
        return False, f"positive_intervals below {thresholds.min_positive_samples}"
    if positive_rate < thresholds.min_positive_rate:
        return False, f"positive_rate below {thresholds.min_positive_rate:g}"
    if positive_rate > thresholds.max_positive_rate:
        return False, f"positive_rate above {thresholds.max_positive_rate:g}"
    if horizon_hours > 0:
        return True, "meets Phase 1.5 horizon recommendation thresholds"
    return True, "meets Phase 1 recommendation thresholds"


def rank_scan_results(results: pd.DataFrame) -> pd.DataFrame:
    """Sort scan rows with recommended and more informative targets first."""
    if results.empty:
        return pd.DataFrame(columns=SCAN_OUTPUT_COLUMNS)

    out = results.copy()
    for col in SCAN_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out["recommended"] = out["recommended"].astype(bool)
    out["horizon_hours"] = pd.to_numeric(out["horizon_hours"], errors="coerce").fillna(0).astype(int)
    out["_last_positive"] = pd.to_datetime(out["last_positive_dispatch_interval"], errors="coerce")
    out = out.sort_values(
        ["recommended", "positive_intervals", "positive_rate", "_last_positive", "duid", "days", "horizon_hours"],
        ascending=[False, False, False, False, True, True, True],
        na_position="last",
    )
    return out[SCAN_OUTPUT_COLUMNS].reset_index(drop=True)


def _normalise_horizons(horizons_hours: Sequence[int]) -> list[int]:
    horizons: list[int] = []
    for value in horizons_hours:
        horizon = int(value)
        if horizon < 0:
            raise ValueError("horizon hours must be non-negative.")
        if horizon not in horizons:
            horizons.append(horizon)
    return horizons or [0]


def _thresholds_for_horizon(
    horizon_hours: int,
    thresholds: ScanThresholds,
    horizon_thresholds: ScanThresholds | None,
) -> ScanThresholds:
    if horizon_hours > 0 and horizon_thresholds is not None:
        return horizon_thresholds
    return thresholds


def discover_candidate_duids(
    dudetail: pd.DataFrame,
    max_duids: int | None = None,
    fallback_file: str | Path = "data/candidate_duids.txt",
) -> list[str]:
    """Build a small, practical default DUID candidate list from metadata."""
    seed_duids = read_duid_file(fallback_file) if Path(fallback_file).exists() else []
    current = _current_rows_by_duid(dudetail)
    metadata_duids = set(current["DUID"].astype(str).str.upper()) if "DUID" in current.columns else set()

    if current.empty:
        return _limit_duids(seed_duids, max_duids)

    ranked = current.copy()
    ranked["_IS_GENERATOR"] = _column(ranked, "DISPATCHTYPE").astype(str).str.upper().eq("GENERATOR")
    ranked["_IS_SEMI"] = _column(ranked, "SEMISCHEDULE_FLAG").astype(str).str.upper().eq("Y")
    ranked["_IS_INTERMITTENT"] = _column(ranked, "INTERMITTENTFLAG").astype(str).str.upper().eq("Y")
    if "MAXCAPACITY" in ranked.columns:
        ranked["_CAPACITY"] = pd.to_numeric(ranked["MAXCAPACITY"], errors="coerce").fillna(0)
    elif "REGISTEREDCAPACITY" in ranked.columns:
        ranked["_CAPACITY"] = pd.to_numeric(ranked["REGISTEREDCAPACITY"], errors="coerce").fillna(0)
    else:
        ranked["_CAPACITY"] = 0

    ranked = ranked[ranked["_IS_GENERATOR"]].copy()
    ranked = ranked.sort_values(
        ["_IS_SEMI", "_IS_INTERMITTENT", "_CAPACITY", "DUID"],
        ascending=[False, False, False, True],
    )
    discovered = ranked["DUID"].astype(str).str.upper().tolist()

    ordered: list[str] = []
    for duid in seed_duids + discovered:
        duid_upper = str(duid).strip().upper()
        if not duid_upper:
            continue
        if duid_upper not in ordered and (duid_upper in metadata_duids or duid in seed_duids):
            ordered.append(duid_upper)
    return _limit_duids(ordered, max_duids)


def parse_duid_list(value: str | None) -> list[str]:
    if not value:
        return []
    return _dedupe_upper(item for item in value.split(","))


def read_duid_file(path: str | Path) -> list[str]:
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        out.append(value)
    return _dedupe_upper(out)


def _dedupe_upper(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip().upper()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _column(df: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index)


def _limit_duids(duids: list[str], max_duids: int | None) -> list[str]:
    if max_duids is None or max_duids <= 0:
        return duids
    return duids[:max_duids]
