"""Forecast/query helpers for the AEMO constraint demo."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import pandas as pd

from .features import add_time_features, numericise_feature_columns
from .weather import fetch_open_meteo_forecast, merge_weather_to_intervals


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


def forecast_from_bundle(
    model_path: str | Path,
    duid: str | None = None,
    horizon_hours: int = 24,
    start: str | None = None,
    timezone: str = "Australia/Melbourne",
) -> dict:
    bundle = joblib.load(model_path)
    trained_duid = str(bundle["duid"]).upper()
    if duid is not None and duid.upper() != trained_duid:
        raise ValueError(
            f"This demo model was trained for {trained_duid}, not {duid.upper()}. "
            "Retrain with python -m src.aemo_constraint_demo.train for the requested DUID."
        )

    intervals = future_intervals(horizon_hours=horizon_hours, start=start, timezone=timezone)
    features = add_time_features(intervals)
    if not bundle.get("no_weather", False):
        forecast_days = max(1, min(16, int(horizon_hours / 24) + 2))
        weather = fetch_open_meteo_forecast(bundle["latitude"], bundle["longitude"], forecast_days=forecast_days, timezone=timezone)
        features = merge_weather_to_intervals(features, weather)

    feature_cols = list(bundle["feature_columns"])
    for col in feature_cols:
        if col not in features.columns:
            features[col] = pd.NA
    features = numericise_feature_columns(features, feature_cols)

    scores = bundle["model"].predict_proba(features[feature_cols])[:, 1]
    planned = planned_constraints_for_intervals(bundle, intervals)

    rows = []
    for ts, prob in zip(intervals["SETTLEMENTDATE"], scores, strict=False):
        ts = pd.Timestamp(ts)
        planned_constraints = planned.get(ts, [])
        rows.append(
            {
                "settlementdate": ts.isoformat(),
                "duid": trained_duid,
                "ml_constraint_probability": round(float(prob), 4),
                "ml_predicted_constrained": bool(prob >= 0.5),
                "aemo_planned_relevant_constraints": planned_constraints,
            }
        )

    return {
        "duid": trained_duid,
        "connection_point": bundle.get("connection_point"),
        "horizon_hours": horizon_hours,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model_trained_at_utc": bundle.get("trained_at_utc"),
        "model_metrics": bundle.get("metrics", {}),
        "relevant_genconids_count": len(bundle.get("relevant_genconids", [])),
        "forecast": rows,
    }
