"""Shared model-frame construction for training, backtesting, and serving.

Training, event-window backtesting, and query-time serving must all build the same
feature row for a given dispatch interval. Keeping one builder here is what prevents
train/serve skew: a feature that exists at training time but not at query time shows
up as an explicit gap rather than a silently imputed median.

The frame is origin-anchored. Row ``t`` carries time features at ``t``, weather at
``t``, and dispatch lags from strictly before ``t``; its horizon label answers
"does a relevant constraint bind in ``(t, t + horizon_hours]``".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd

from .aemo_client import load_next_day_dispatch_tables
from .features import (
    add_feature_set_features,
    build_label_frame,
    numericise_feature_columns,
    relevant_constraints_for_duid,
    select_feature_columns,
)
from .scan import add_horizon_labels, horizon_label_column
from .weather import fetch_open_meteo_history, merge_weather_to_intervals

INTERVALS_PER_HOUR = 12
CURRENT_INTERVAL_TARGET = "target_constrained"


@dataclass(frozen=True)
class ModelFrame:
    """A feature frame plus everything a caller needs to fit or score against it."""

    frame: pd.DataFrame
    feature_columns: list[str]
    target_column: str
    connection_point: str | None
    relevant_genconids: list[str]
    lhs_rows: pd.DataFrame | None = None
    metadata: dict = field(default_factory=dict)


def build_model_frame(
    duid: str,
    days: int,
    horizon_hours: int = 0,
    *,
    feature_set: str = "time_weather",
    include_dispatch_features: bool = False,
    bind_threshold: float = 0.0,
    no_weather: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
    source: str = "network",
    dudetail: pd.DataFrame | None = None,
    spd_connectionpoint: pd.DataFrame | None = None,
    dispatchload: pd.DataFrame | None = None,
    dispatchconstraint: pd.DataFrame | None = None,
    relevant_genconids: Sequence[str] | None = None,
    connection_point: str | None = None,
    drop_incomplete_horizon: bool = True,
    weather_required: bool = True,
) -> ModelFrame:
    """Build the canonical feature frame for one DUID over a recent dispatch window.

    The DUID -> constraint mapping comes from either ``relevant_genconids`` (a trained
    bundle already carries it, alongside ``connection_point``) or the standing-data
    tables ``dudetail``/``spd_connectionpoint``. Dispatch rows are downloaded unless
    ``dispatchload``/``dispatchconstraint`` are supplied, which lets a caller that has
    already loaded a window reuse it.

    ``drop_incomplete_horizon`` controls the tail. Training drops the final
    ``horizon_hours`` of rows because their labels are not yet knowable. Serving must
    keep them: the forecast origin is precisely the newest of those rows.

    ``weather_required`` decides what an Open-Meteo failure means. Training fails loudly
    so a model is never fitted on silently-missing weather; serving passes ``False`` and
    degrades to imputed weather features, which ``metadata["weather_error"]`` records.
    """
    horizon_hours = int(horizon_hours)
    if horizon_hours < 0:
        raise ValueError("horizon_hours must be non-negative.")

    lhs_rows: pd.DataFrame | None = None
    if relevant_genconids is None:
        if dudetail is None or spd_connectionpoint is None:
            raise ValueError(
                "build_model_frame needs either relevant_genconids or both dudetail and "
                "spd_connectionpoint to resolve the DUID -> constraint mapping."
            )
        relevant, resolved_connection_point, lhs_rows = relevant_constraints_for_duid(
            duid, dudetail, spd_connectionpoint
        )
    else:
        relevant = [str(item).upper() for item in relevant_genconids]
        resolved_connection_point = connection_point
        if not relevant:
            raise ValueError(f"No relevant GENCONID values supplied for DUID {duid.upper()}.")

    if dispatchload is None or dispatchconstraint is None:
        dispatch = load_next_day_dispatch_tables(
            days=days,
            table_names=["DISPATCHLOAD", "DISPATCHCONSTRAINT"],
            cache_dir=cache_dir,
            end_date=end_date,
            source=source,
        )
        dispatchload = dispatch["DISPATCHLOAD"]
        dispatchconstraint = dispatch["DISPATCHCONSTRAINT"]

    frame = build_label_frame(
        dispatchload=dispatchload,
        dispatchconstraint=dispatchconstraint,
        duid=duid,
        relevant_genconids=relevant,
        bind_threshold=bind_threshold,
    )

    target_column = CURRENT_INTERVAL_TARGET
    dropped_incomplete_horizon_rows = 0
    if horizon_hours > 0:
        frame = add_horizon_labels(frame, [horizon_hours], intervals_per_hour=INTERVALS_PER_HOUR)
        target_column = horizon_label_column(horizon_hours)
        dropped_incomplete_horizon_rows = min(len(frame), horizon_hours * INTERVALS_PER_HOUR)
        if dropped_incomplete_horizon_rows:
            tail_index = frame.tail(dropped_incomplete_horizon_rows).index
            frame.loc[tail_index, target_column] = pd.NA

    frame = add_feature_set_features(frame, feature_set=feature_set, intervals_per_hour=INTERVALS_PER_HOUR)

    weather_used = False
    weather_error: str | None = None
    if not no_weather:
        start = frame["SETTLEMENTDATE"].min().date()
        end = frame["SETTLEMENTDATE"].max().date()
        try:
            weather = fetch_open_meteo_history(latitude, longitude, start, end)
        except Exception as exc:  # noqa: BLE001 - serving degrades, training fails loudly
            if weather_required:
                raise
            weather = pd.DataFrame()
            weather_error = f"{type(exc).__name__}: {exc}"
        frame = merge_weather_to_intervals(frame, weather)
        weather_used = not weather.empty

    feature_columns = select_feature_columns(
        frame,
        include_dispatch_features=include_dispatch_features,
        feature_set=feature_set,
    )
    if not feature_columns:
        raise ValueError("No feature columns available after feature engineering.")

    frame = numericise_feature_columns(frame, feature_columns)
    subset = ["SETTLEMENTDATE", target_column] if drop_incomplete_horizon else ["SETTLEMENTDATE"]
    frame = frame.dropna(subset=subset).sort_values("SETTLEMENTDATE").reset_index(drop=True)

    metadata = {
        "duid": duid.upper(),
        "days": int(days),
        "horizon_hours": horizon_hours,
        "feature_set": feature_set,
        "dropped_incomplete_horizon_rows": dropped_incomplete_horizon_rows,
        "weather_used": weather_used,
        "weather_error": weather_error,
        "source": source,
        "first_interval": frame["SETTLEMENTDATE"].min() if not frame.empty else None,
        "last_interval": frame["SETTLEMENTDATE"].max() if not frame.empty else None,
    }
    return ModelFrame(
        frame=frame,
        feature_columns=feature_columns,
        target_column=target_column,
        connection_point=resolved_connection_point,
        relevant_genconids=list(relevant),
        lhs_rows=lhs_rows,
        metadata=metadata,
    )
