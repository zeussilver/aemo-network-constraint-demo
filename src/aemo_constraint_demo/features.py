"""Feature engineering and label construction for the AEMO constraint demo."""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

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

TIME_FEATURE_COLUMNS = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
    "is_weekend",
]

WEATHER_FEATURE_COLUMNS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_10m",
    "shortwave_radiation",
]

OPTIONAL_DISPATCH_FEATURE_COLUMNS = [
    "TOTALCLEARED",
    "INITIALMW",
    "AVAILABILITY",
    "UIGF",
    "RAMPUPRATE",
    "RAMPDOWNRATE",
]

LAG_WINDOWS_HOURS = (1, 3, 6)

FEATURE_SET_NAMES = [
    "time_only",
    "time_weather",
    "time_weather_dispatch_lag",
    "time_weather_constraint_lag",
    "time_weather_dispatch_constraint_lag",
]

FEATURE_SET_CHOICES = tuple(FEATURE_SET_NAMES)


def standardise_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(col).strip().upper() for col in out.columns]
    return out


def parse_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def get_current_connection_point(dudetail: pd.DataFrame, duid: str) -> str:
    dudetail = standardise_columns(dudetail)
    if "DUID" not in dudetail.columns:
        raise ValueError("DUDETAIL table does not contain a DUID column.")
    rows = dudetail[dudetail["DUID"].astype(str).str.upper() == duid.upper()].copy()
    if rows.empty:
        raise ValueError(f"DUID {duid} was not found in DUDETAIL.")

    if "EFFECTIVEDATE" in rows.columns:
        rows["_EFFECTIVEDATE"] = parse_datetime(rows["EFFECTIVEDATE"])
    else:
        rows["_EFFECTIVEDATE"] = pd.Timestamp.min
    if "VERSIONNO" in rows.columns:
        rows["_VERSIONNO"] = pd.to_numeric(rows["VERSIONNO"], errors="coerce").fillna(-1)
    else:
        rows["_VERSIONNO"] = -1

    current = rows.sort_values(["_EFFECTIVEDATE", "_VERSIONNO"]).iloc[-1]
    for col in ("CONNECTIONPOINTID", "CONNECTION_POINT_ID", "CONNECTIONPOINT"):
        if col in current.index and pd.notna(current[col]) and str(current[col]).strip():
            return str(current[col]).strip().upper()
    # Some simple public examples use a DUID-like connection point. Keep a fallback.
    return duid.upper()


def relevant_constraints_for_duid(
    duid: str,
    dudetail: pd.DataFrame,
    spd_connectionpoint: pd.DataFrame,
    prefer_energy_terms: bool = True,
) -> tuple[list[str], str, pd.DataFrame]:
    """Map DUID -> connection point -> GENCONID list.

    SPDCONNECTIONPOINTCONSTRAINT is the LHS table linking connection points to
    generic constraints. For an energy constraint forecast, non-FCAS BIDTYPE rows
    are preferred where present.
    """
    spd = standardise_columns(spd_connectionpoint)
    connection_point = get_current_connection_point(dudetail, duid)
    required = {"CONNECTIONPOINTID", "GENCONID"}
    missing = required - set(spd.columns)
    if missing:
        raise ValueError(f"SPDCONNECTIONPOINTCONSTRAINT is missing columns: {sorted(missing)}")

    rows = spd[spd["CONNECTIONPOINTID"].astype(str).str.upper() == connection_point].copy()
    if rows.empty:
        raise ValueError(
            f"No SPDCONNECTIONPOINTCONSTRAINT rows found for DUID {duid} "
            f"connection point {connection_point}."
        )

    if "FACTOR" in rows.columns:
        rows["_FACTOR"] = pd.to_numeric(rows["FACTOR"], errors="coerce").fillna(0)
        rows = rows[rows["_FACTOR"].abs() > 0]

    if prefer_energy_terms and "BIDTYPE" in rows.columns:
        bidtype = rows["BIDTYPE"].fillna("").astype(str).str.upper().str.strip()
        non_fcas = rows[~bidtype.isin(FCAS_BIDTYPES)]
        if not non_fcas.empty:
            rows = non_fcas

    constraints = sorted(rows["GENCONID"].dropna().astype(str).str.upper().unique().tolist())
    if not constraints:
        raise ValueError(f"No relevant GENCONID values found for DUID {duid}.")
    return constraints, connection_point, rows.reset_index(drop=True)


def build_label_frame(
    dispatchload: pd.DataFrame,
    dispatchconstraint: pd.DataFrame,
    duid: str,
    relevant_genconids: Iterable[str],
    bind_threshold: float = 0.0,
) -> pd.DataFrame:
    """Build a DUID-level interval frame with target_constrained labels.

    Label definition:
      1 if any relevant GENCONID has MARGINALVALUE > bind_threshold or
      VIOLATIONDEGREE > 0 at the interval; otherwise 0.
    """
    relevant = {str(item).upper() for item in relevant_genconids}
    load = standardise_columns(dispatchload)
    con = standardise_columns(dispatchconstraint)

    if load.empty:
        raise ValueError("DISPATCHLOAD is empty; cannot build interval grid.")
    if "DUID" not in load.columns or "SETTLEMENTDATE" not in load.columns:
        raise ValueError("DISPATCHLOAD must contain DUID and SETTLEMENTDATE.")

    base = load[load["DUID"].astype(str).str.upper() == duid.upper()].copy()
    if base.empty:
        raise ValueError(f"No DISPATCHLOAD rows found for DUID {duid}.")
    base["SETTLEMENTDATE"] = parse_datetime(base["SETTLEMENTDATE"])
    base = base.dropna(subset=["SETTLEMENTDATE"]).sort_values("SETTLEMENTDATE")

    # Keep one row per interval if duplicate dispatch versions appear.
    base = base.drop_duplicates(subset=["SETTLEMENTDATE"], keep="last")

    if con.empty or "CONSTRAINTID" not in con.columns:
        base["target_constrained"] = 0
        base["binding_constraint_ids"] = ""
        base["max_marginal_value"] = 0.0
        base["max_violation_degree"] = 0.0
        base["binding_constraint_count"] = 0
        return base

    con = con.copy()
    con["CONSTRAINTID"] = con["CONSTRAINTID"].astype(str).str.upper()
    con = con[con["CONSTRAINTID"].isin(relevant)]
    if "INTERVENTION" in con.columns:
        con = con[con["INTERVENTION"].astype(str).str.strip().isin(["0", "", "nan", "None"])]
    if con.empty:
        base["target_constrained"] = 0
        base["binding_constraint_ids"] = ""
        base["max_marginal_value"] = 0.0
        base["max_violation_degree"] = 0.0
        base["binding_constraint_count"] = 0
        return base

    con["SETTLEMENTDATE"] = parse_datetime(con["SETTLEMENTDATE"])
    con["MARGINALVALUE"] = pd.to_numeric(con.get("MARGINALVALUE", 0), errors="coerce").fillna(0)
    con["VIOLATIONDEGREE"] = pd.to_numeric(con.get("VIOLATIONDEGREE", 0), errors="coerce").fillna(0)
    con["_IS_BINDING"] = (con["MARGINALVALUE"] > bind_threshold) | (con["VIOLATIONDEGREE"] > 0)
    con = con[con["_IS_BINDING"]]

    grouped = (
        con.groupby("SETTLEMENTDATE")
        .agg(
            target_constrained=("_IS_BINDING", "max"),
            binding_constraint_ids=("CONSTRAINTID", lambda values: ";".join(sorted(set(values)))),
            max_marginal_value=("MARGINALVALUE", "max"),
            max_violation_degree=("VIOLATIONDEGREE", "max"),
            binding_constraint_count=("CONSTRAINTID", "nunique"),
        )
        .reset_index()
    )
    grouped["target_constrained"] = grouped["target_constrained"].astype(int)

    out = base.merge(grouped, on="SETTLEMENTDATE", how="left")
    out["target_constrained"] = out["target_constrained"].fillna(0).astype(int)
    out["binding_constraint_ids"] = out["binding_constraint_ids"].fillna("")
    out["max_marginal_value"] = out["max_marginal_value"].fillna(0.0)
    out["max_violation_degree"] = out["max_violation_degree"].fillna(0.0)
    out["binding_constraint_count"] = out["binding_constraint_count"].fillna(0).astype(int)
    return out.sort_values("SETTLEMENTDATE").reset_index(drop=True)


def add_time_features(df: pd.DataFrame, time_col: str = "SETTLEMENTDATE") -> pd.DataFrame:
    out = df.copy()
    ts = parse_datetime(out[time_col])
    hour_float = ts.dt.hour + ts.dt.minute / 60.0
    out["hour_sin"] = np.sin(2 * math.pi * hour_float / 24.0)
    out["hour_cos"] = np.cos(2 * math.pi * hour_float / 24.0)
    out["dow_sin"] = np.sin(2 * math.pi * ts.dt.dayofweek / 7.0)
    out["dow_cos"] = np.cos(2 * math.pi * ts.dt.dayofweek / 7.0)
    out["month_sin"] = np.sin(2 * math.pi * ts.dt.month / 12.0)
    out["month_cos"] = np.cos(2 * math.pi * ts.dt.month / 12.0)
    out["is_weekend"] = (ts.dt.dayofweek >= 5).astype(int)
    return out


def numericise_feature_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _is_target_like_column(column: str) -> bool:
    normalized = str(column).strip().lower()
    return normalized == "target_constrained" or normalized.startswith("target_constrained_next_")


def _safe_existing_columns(df: pd.DataFrame, candidates: Iterable[str]) -> list[str]:
    return [col for col in candidates if col in df.columns and not _is_target_like_column(col)]


def dispatch_lag_feature_columns(df: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for source_col in OPTIONAL_DISPATCH_FEATURE_COLUMNS:
        for hours in LAG_WINDOWS_HOURS:
            columns.append(f"{source_col.lower()}_lag_{hours}h")
            columns.append(f"{source_col.lower()}_rolling_mean_{hours}h")
    return _safe_existing_columns(df, columns)


def constraint_lag_feature_columns(df: pd.DataFrame) -> list[str]:
    candidates: list[str] = []
    for hours in LAG_WINDOWS_HOURS:
        candidates.extend(
            [
                f"target_constrained_lag_{hours}h",
                f"target_constrained_roll_count_{hours}h",
                f"max_marginal_value_lag_{hours}h",
                f"max_marginal_value_rolling_max_{hours}h",
                f"max_violation_degree_lag_{hours}h",
                f"max_violation_degree_rolling_max_{hours}h",
                f"binding_constraint_count_lag_{hours}h",
                f"binding_constraint_count_rolling_sum_{hours}h",
            ]
        )
    candidates.append("target_constrained_hours_since_last")
    return _safe_existing_columns(df, candidates)


def add_lag_features(
    df: pd.DataFrame,
    intervals_per_hour: int = 12,
    time_col: str = "SETTLEMENTDATE",
) -> pd.DataFrame:
    """Add leakage-safe lag and rolling features.

    Rolling features are shifted by one interval before aggregation, so the
    current row's label never contributes to its own model features.
    """
    out = df.copy()
    if out.empty:
        return out

    out = out.sort_values(time_col).reset_index(drop=True) if time_col in out.columns else out.reset_index(drop=True)
    intervals_per_hour = int(intervals_per_hour)
    if intervals_per_hour <= 0:
        raise ValueError("intervals_per_hour must be positive.")

    for source_col in OPTIONAL_DISPATCH_FEATURE_COLUMNS:
        if source_col not in out.columns:
            continue
        values = pd.to_numeric(out[source_col], errors="coerce")
        shifted = values.shift(1)
        for hours in LAG_WINDOWS_HOURS:
            window = max(1, int(hours * intervals_per_hour))
            out[f"{source_col.lower()}_lag_{hours}h"] = values.shift(window)
            out[f"{source_col.lower()}_rolling_mean_{hours}h"] = shifted.rolling(window=window, min_periods=1).mean()

    if "target_constrained" in out.columns:
        target = pd.to_numeric(out["target_constrained"], errors="coerce").fillna(0).astype(int)
        shifted_target = target.shift(1)
        for hours in LAG_WINDOWS_HOURS:
            window = max(1, int(hours * intervals_per_hour))
            out[f"target_constrained_lag_{hours}h"] = target.shift(window).fillna(0).astype(int)
            out[f"target_constrained_roll_count_{hours}h"] = (
                shifted_target.rolling(window=window, min_periods=1).sum().fillna(0)
            )

        if time_col in out.columns:
            timestamps = parse_datetime(out[time_col])
            previous_positive_time = timestamps.shift(1).where(shifted_target.eq(1)).ffill()
            out["target_constrained_hours_since_last"] = (
                (timestamps - previous_positive_time).dt.total_seconds() / 3600.0
            )
        else:
            previous_positive_index = pd.Series(np.where(shifted_target.eq(1), out.index, np.nan)).ffill()
            out["target_constrained_hours_since_last"] = (
                (pd.Series(out.index, index=out.index) - previous_positive_index) / intervals_per_hour
            )

    constraint_value_sources = {
        "max_marginal_value": "max",
        "max_violation_degree": "max",
        "binding_constraint_count": "sum",
    }
    for source_col, aggregate in constraint_value_sources.items():
        if source_col not in out.columns:
            continue
        values = pd.to_numeric(out[source_col], errors="coerce").fillna(0.0)
        shifted = values.shift(1)
        for hours in LAG_WINDOWS_HOURS:
            window = max(1, int(hours * intervals_per_hour))
            out[f"{source_col}_lag_{hours}h"] = values.shift(window).fillna(0.0)
            if aggregate == "sum":
                out[f"{source_col}_rolling_sum_{hours}h"] = shifted.rolling(window=window, min_periods=1).sum().fillna(0.0)
            else:
                out[f"{source_col}_rolling_max_{hours}h"] = shifted.rolling(window=window, min_periods=1).max().fillna(0.0)

    return out


def add_feature_set_features(
    df: pd.DataFrame,
    feature_set: str = "time_weather",
    intervals_per_hour: int = 12,
) -> pd.DataFrame:
    if feature_set not in FEATURE_SET_CHOICES:
        raise ValueError(f"Unknown feature_set {feature_set!r}. Choose one of {list(FEATURE_SET_CHOICES)}.")
    out = add_time_features(df)
    if "lag" in feature_set:
        out = add_lag_features(out, intervals_per_hour=intervals_per_hour)
    return out


def select_feature_columns(
    df: pd.DataFrame,
    include_dispatch_features: bool = False,
    feature_set: str | None = None,
) -> list[str]:
    if feature_set is None:
        candidates = list(TIME_FEATURE_COLUMNS) + list(WEATHER_FEATURE_COLUMNS)
        if include_dispatch_features:
            candidates += OPTIONAL_DISPATCH_FEATURE_COLUMNS
        return _safe_existing_columns(df, candidates)

    if feature_set not in FEATURE_SET_CHOICES:
        raise ValueError(f"Unknown feature_set {feature_set!r}. Choose one of {list(FEATURE_SET_CHOICES)}.")

    candidates = list(TIME_FEATURE_COLUMNS)
    if feature_set != "time_only":
        candidates += WEATHER_FEATURE_COLUMNS
    if include_dispatch_features:
        candidates += OPTIONAL_DISPATCH_FEATURE_COLUMNS
    if "dispatch" in feature_set:
        candidates += dispatch_lag_feature_columns(df)
    if "constraint" in feature_set:
        candidates += constraint_lag_feature_columns(df)
    return _safe_existing_columns(df, candidates)
