"""Forecast/query helpers for the AEMO constraint demo.

The model is origin-anchored: it scores the state at one dispatch interval ``t`` and
estimates whether a relevant generic constraint binds in ``(t, t + horizon_hours]``.
Serving therefore builds the same feature row training would build for ``t`` — via the
shared :mod:`~src.aemo_constraint_demo.frames` builder — rather than inventing rows for
future intervals whose dispatch lags cannot exist yet.
"""
from __future__ import annotations

import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import pandas as pd

from .features import numericise_feature_columns
from .frames import INTERVALS_PER_HOUR, build_model_frame


FALLBACK_WEATHER_WARNING = "Open-Meteo history unavailable; weather features were imputed."
DEFAULT_HISTORY_DAYS = 2
RISK_HISTORY_HOURS = 24


def _next_5min_timestamp(timezone: str = "Australia/Melbourne") -> pd.Timestamp:
    now = pd.Timestamp.now(tz=ZoneInfo(timezone)).ceil("5min")
    return now.tz_localize(None)


def future_intervals(horizon_hours: int, start: str | None = None, timezone: str = "Australia/Melbourne") -> pd.DataFrame:
    if start is None:
        start_ts = _next_5min_timestamp(timezone=timezone)
    else:
        start_ts = pd.to_datetime(start)
    periods = int(horizon_hours * 12)
    if periods <= 0:
        raise ValueError("horizon_hours must be positive.")
    return pd.DataFrame({"SETTLEMENTDATE": pd.date_range(start=start_ts, periods=periods, freq="5min")})


def risk_level(probability: float) -> str:
    """Map a probability to a compact demo risk band."""
    if probability >= 0.5:
        return "high"
    if probability >= 0.2:
        return "medium"
    if probability >= 0.05:
        return "low"
    return "very_low"


def _market_period_to_timestamp(market_date, period) -> pd.Timestamp | None:
    if pd.isna(market_date) or pd.isna(period):
        return None
    try:
        p = int(float(period))
    except Exception:  # noqa: BLE001
        return None
    # Dispatch period 1 is the interval ending 04:05 for the market date.
    base = pd.to_datetime(market_date).normalize() + pd.Timedelta(hours=4)
    return base + pd.Timedelta(minutes=5 * p)


def planned_constraints_for_intervals(bundle: dict, intervals: pd.DataFrame) -> dict[pd.Timestamp, list[str]]:
    """Return active planned/invoked relevant GENCONIDs per future interval.

    This uses GENCONSETINVOKE as a lightweight public-data proxy for planned constraints.
    """
    inv = bundle.get("planned_genconsetinvoke")
    gset = bundle.get("planned_genconset")
    relevant = set(bundle.get("relevant_genconids", []))
    if inv is None or gset is None or inv.empty or gset.empty:
        return {pd.Timestamp(ts): [] for ts in intervals["SETTLEMENTDATE"]}

    inv = inv.copy()
    gset = gset.copy()
    inv.columns = [str(c).strip().upper() for c in inv.columns]
    gset.columns = [str(c).strip().upper() for c in gset.columns]
    if "GENCONSETID" not in inv.columns or "GENCONSETID" not in gset.columns or "GENCONID" not in gset.columns:
        return {pd.Timestamp(ts): [] for ts in intervals["SETTLEMENTDATE"]}

    relevant_set_rows = gset[gset["GENCONID"].astype(str).str.upper().isin(relevant)].copy()
    set_to_constraints = (
        relevant_set_rows.groupby(relevant_set_rows["GENCONSETID"].astype(str).str.upper())["GENCONID"]
        .apply(lambda values: sorted(set(str(v).upper() for v in values)))
        .to_dict()
    )
    if not set_to_constraints:
        return {pd.Timestamp(ts): [] for ts in intervals["SETTLEMENTDATE"]}

    planned_by_ts: dict[pd.Timestamp, list[str]] = {}
    for ts in intervals["SETTLEMENTDATE"]:
        timestamp = pd.Timestamp(ts)
        active: set[str] = set()
        for _, row in inv.iterrows():
            if "STARTAUTHORISEDBY" in row.index and not str(row.get("STARTAUTHORISEDBY", "")).strip():
                continue
            set_id = str(row.get("GENCONSETID", "")).upper()
            if set_id not in set_to_constraints:
                continue
            start = _market_period_to_timestamp(row.get("STARTDATE"), row.get("STARTPERIOD"))
            end = _market_period_to_timestamp(row.get("ENDDATE"), row.get("ENDPERIOD"))
            if start is None:
                continue
            if end is None:
                end = pd.Timestamp.max.normalize()
            if start <= timestamp <= end:
                active.update(set_to_constraints[set_id])
        planned_by_ts[timestamp] = sorted(active)
    return planned_by_ts


def _resolve_horizon(bundle: dict, horizon_hours: int | None) -> int:
    bundle_horizon = bundle.get("horizon_hours", bundle.get("metrics", {}).get("horizon_hours"))
    if bundle_horizon is not None:
        bundle_horizon = int(bundle_horizon)
    resolved = int(horizon_hours) if horizon_hours is not None else (bundle_horizon or 24)
    if resolved <= 0:
        raise ValueError("horizon_hours must be positive.")
    if bundle_horizon is not None and horizon_hours is not None and resolved != bundle_horizon:
        raise ValueError(
            f"Requested horizon_hours={resolved} does not match model bundle horizon_hours={bundle_horizon}. "
            "Omit horizon_hours to use the model horizon, or train a matching model bundle."
        )
    return resolved


def summarise_planned_constraints(bundle: dict, origin: pd.Timestamp, horizon_hours: int) -> list[dict]:
    """Aggregate the invoked-constraint timeline over ``(origin, origin + horizon]``."""
    intervals = future_intervals(
        horizon_hours=horizon_hours,
        start=str(origin + pd.Timedelta(minutes=5)),
    )
    by_interval = planned_constraints_for_intervals(bundle, intervals)
    active: dict[str, list[pd.Timestamp]] = {}
    for timestamp, genconids in by_interval.items():
        for genconid in genconids:
            active.setdefault(genconid, []).append(timestamp)
    return [
        {
            "genconid": genconid,
            "active_intervals": len(timestamps),
            "first_interval": min(timestamps).isoformat(),
            "last_interval": max(timestamps).isoformat(),
        }
        for genconid, timestamps in sorted(active.items())
    ]


def _lead_time_risk_curve(
    bundle: dict,
    origin_features: pd.DataFrame,
    origin: pd.Timestamp,
) -> tuple[list[dict], dict | None]:
    """Score each lead-time bucket from the one origin row.

    Buckets tile the horizon with disjoint windows, so a bundle that carries them turns a
    single origin into a forward risk curve. Every bucket sees the same observed features,
    which is what keeps the curve free of the skew a per-future-interval scoring pass had.
    """
    lead_models = bundle.get("lead_models") or {}
    if not lead_models:
        return [], None

    bucket_hours = int(bundle.get("lead_bucket_hours") or 1)
    curve: list[dict] = []
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"sklearn\..*")
        for lead in sorted(lead_models):
            probability = float(lead_models[lead].predict_proba(origin_features)[:, 1][0])
            curve.append(
                {
                    "lead_hours": int(lead),
                    "window_start": (origin + pd.Timedelta(hours=int(lead) - bucket_hours)).isoformat(),
                    "window_end": (origin + pd.Timedelta(hours=int(lead))).isoformat(),
                    "probability": round(probability, 4),
                    "risk_level": risk_level(probability),
                }
            )

    peak = max(curve, key=lambda bucket: bucket["probability"])
    return curve, {
        "lead_hours": peak["lead_hours"],
        "window_start": peak["window_start"],
        "window_end": peak["window_end"],
        "probability": peak["probability"],
        "risk_level": peak["risk_level"],
    }


def _risk_history(frame: pd.DataFrame, scores, hours: int = RISK_HISTORY_HOURS) -> list[dict]:
    """Hourly risk trajectory over the trailing window, oldest first."""
    step = INTERVALS_PER_HOUR
    points = min(len(frame), hours + 1)
    positions = sorted({max(0, len(frame) - 1 - offset * step) for offset in range(points)})
    return [
        {
            "origin": pd.Timestamp(frame["SETTLEMENTDATE"].iloc[position]).isoformat(),
            "probability": round(float(scores[position]), 4),
            "risk_level": risk_level(float(scores[position])),
        }
        for position in positions
    ]


def forecast_from_bundle(
    model_path: str | Path,
    duid: str | None = None,
    horizon_hours: int | None = None,
    timezone: str = "Australia/Melbourne",
    use_weather: bool = True,
    history_days: int = DEFAULT_HISTORY_DAYS,
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
    source: str = "auto",
) -> dict:
    """Score the latest observed dispatch interval for a DUID's near-term constraint risk.

    Returns one probability - the quantity the model was trained to estimate - anchored at
    the newest dispatch interval available from NEMWeb, plus the invoked-constraint
    timeline over the horizon and the trailing risk trajectory.

    A bundle carrying lead-time bucket models additionally returns ``risk_curve``: one
    probability per disjoint window inside the horizon, all scored from that same origin.
    """
    bundle = joblib.load(model_path)
    trained_duid = str(bundle["duid"]).upper()
    if duid is not None and duid.upper() != trained_duid:
        raise ValueError(
            f"This demo model was trained for {trained_duid}, not {duid.upper()}. "
            "Retrain with python -m src.aemo_constraint_demo.train for the requested DUID."
        )

    resolved_horizon = _resolve_horizon(bundle, horizon_hours)
    bundle_no_weather = bool(bundle.get("no_weather", False))
    model_frame = build_model_frame(
        duid=trained_duid,
        days=int(history_days),
        horizon_hours=resolved_horizon,
        feature_set=bundle.get("feature_set") or "time_weather",
        include_dispatch_features=bool(bundle.get("include_dispatch_features", False)),
        bind_threshold=float(bundle.get("bind_threshold") or 0.0),
        no_weather=bundle_no_weather or not use_weather,
        latitude=bundle.get("latitude"),
        longitude=bundle.get("longitude"),
        cache_dir=cache_dir,
        end_date=end_date,
        source=source,
        relevant_genconids=bundle.get("relevant_genconids"),
        connection_point=bundle.get("connection_point"),
        drop_incomplete_horizon=False,
        weather_required=False,
    )

    frame = model_frame.frame
    if frame.empty:
        raise ValueError(
            f"No dispatch history found for {trained_duid} in the last {history_days} trading day(s). "
            "Increase history_days, or use source='network' to download fresh NEMWeb files."
        )

    feature_cols = list(bundle["feature_columns"])
    for col in feature_cols:
        if col not in frame.columns:
            frame[col] = pd.NA
    frame = numericise_feature_columns(frame, feature_cols)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"sklearn\..*")
        scores = bundle["model"].predict_proba(frame[feature_cols])[:, 1]

    origin = pd.Timestamp(frame["SETTLEMENTDATE"].iloc[-1])
    horizon_end = origin + pd.Timedelta(hours=resolved_horizon)
    probability = round(float(scores[-1]), 4)
    now = pd.Timestamp.now(tz=ZoneInfo(timezone)).tz_localize(None)

    origin_features = frame.iloc[-1][feature_cols]
    imputed = sorted(col for col in feature_cols if pd.isna(origin_features[col]))

    risk_curve, peak_lead = _lead_time_risk_curve(bundle, frame[feature_cols].iloc[[-1]], origin)

    if bundle_no_weather:
        weather_source = "not_used_no_weather_bundle"
    elif not use_weather:
        weather_source = "not_requested"
    elif model_frame.metadata["weather_used"]:
        weather_source = "open_meteo_history"
    else:
        weather_source = "fallback_imputed"
    weather_warning = None
    if weather_source in {"not_requested", "fallback_imputed"}:
        weather_warning = FALLBACK_WEATHER_WARNING

    metrics = bundle.get("metrics", {})
    relevant = list(bundle.get("relevant_genconids", []))
    return {
        "duid": trained_duid,
        "connection_point": bundle.get("connection_point"),
        "horizon_hours": resolved_horizon,
        "as_of": origin.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "data_lag_hours": round((now - origin).total_seconds() / 3600.0, 2),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint_risk": {
            "probability": probability,
            "risk_level": risk_level(float(scores[-1])),
            "statement": (
                f"P(a relevant generic constraint binds for {trained_duid} between "
                f"{origin.isoformat()} and {horizon_end.isoformat()})"
            ),
        },
        "feature_coverage": {
            "required": len(feature_cols),
            "observed": len(feature_cols) - len(imputed),
            "imputed": len(imputed),
            "imputed_examples": imputed[:5],
        },
        "dispatch_source": {
            "source": source,
            "history_days": int(history_days),
            "first_interval": pd.Timestamp(frame["SETTLEMENTDATE"].iloc[0]).isoformat(),
            "last_interval": origin.isoformat(),
            "interval_count": int(len(frame)),
        },
        "weather_source": weather_source,
        "weather_warning": weather_warning,
        "model": {
            "model_type": metrics.get("model_type"),
            "feature_set": bundle.get("feature_set"),
            "lead_hours": sorted(bundle.get("lead_models") or {}) or None,
            "target_column": bundle.get("target_column", metrics.get("target_column")),
            "horizon_hours": bundle.get("horizon_hours", metrics.get("horizon_hours")),
            "positive_rate": metrics.get("positive_rate"),
            "trained_at_utc": bundle.get("trained_at_utc"),
        },
        "model_trained_at_utc": bundle.get("trained_at_utc"),
        "model_metrics": metrics,
        "relevant_genconids": relevant,
        "relevant_genconids_count": len(relevant),
        "planned_constraints": summarise_planned_constraints(bundle, origin, resolved_horizon),
        "planned_constraints_source": "GENCONSETINVOKE relevant-set proxy when available",
        "risk_curve": risk_curve,
        "peak_lead": peak_lead,
        "recent_risk_history": _risk_history(frame, scores),
        "limitations": (
            "Demo model. Out-of-sample evidence is the event-window backtest (mean AP 0.615, "
            "lift@top-5% 1.94); the chronological holdout contains no positive labels. The origin "
            "trails real time because NEMWeb Next Day Dispatch publishes the previous trading day - "
            "see data_lag_hours."
        ),
    }
