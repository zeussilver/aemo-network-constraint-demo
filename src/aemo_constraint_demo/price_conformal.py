"""Recalibrate a quantile forecast against its own recent errors.

The quantile model in `price_model.py` is the sharpest forecast in this lane and, of the
ones worth using, the worst calibrated: its 80% interval holds the settled price 65% of the
time. That is a fixable failure, and fixing it does not need a better model. If the 90th
percentile has been too low all month, the honest correction is to move it up by however
much it was out — which is what split conformal prediction does, and why it comes with a
coverage guarantee rather than a hope.

What follows is the quantile form of it, applied one level at a time. For a level the
residual is ``actual − forecast``; pooling those residuals over a trailing window and taking
their own quantile at that same level gives the offset that would have made the level honest
over the window. Adding it and re-sorting leaves a valid, non-crossing distribution, and the
median moves only as far as the median residual, so sharpness is not traded away for
coverage the way averaging distributions trades it.

"Trailing window" means what it means in `price_ensemble.py`: **complete days that had
already settled when the row was published**. A row published on day D is corrected from
days up to D−1 and never from its own outcome, so the recalibrated forecast is one that
could actually have been published. Rows too early in the record for a window to exist are
left exactly as the model produced them rather than borrowing a correction from the future.

The layer is deliberately cheap. It reads the scored rows a backtest has already saved and
refits nothing, so asking whether a forecast is honest costs seconds rather than the hours
the forecast itself took.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .price_model import enforce_non_crossing

DEFAULT_WINDOW_DAYS = 28  # four weeks: long enough for a 5th percentile, short enough to follow a season
MIN_SETTLED_DAYS = 3  # fewer than this and the offsets are a reading of three days' weather
MIN_RESIDUALS = 500  # and fewer than this leaves the outer levels fitted to a handful of points


def residual_frame(
    frame: pd.DataFrame,
    predictions: Mapping[float, Sequence[float]],
    settled_column: str = "target",
    actual_column: str = "actual_rrp",
) -> pd.DataFrame:
    """Per scored row: the day its interval settled, and how far the price fell from each quantile."""
    actual = frame[actual_column].to_numpy(dtype=float)
    residuals = pd.DataFrame({"day": frame[settled_column].dt.floor("D").to_numpy()})
    for level in sorted(predictions):
        residuals[str(level)] = actual - np.asarray(predictions[level], dtype=float)
    return residuals


def rolling_offsets(
    residuals: pd.DataFrame,
    levels: Sequence[float],
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_days: int = MIN_SETTLED_DAYS,
    min_residuals: int = MIN_RESIDUALS,
) -> pd.DataFrame:
    """For each day, the offset per level implied by the days that had settled before it.

    A day's offsets are read from the previous ``window_days`` settled days only, never from
    the day itself, so they are knowable to a forecast published that morning. Days with too
    little history behind them are left out of the table entirely; `offsets_for_rows` reads
    that absence as "no correction".
    """
    columns = [str(level) for level in levels]
    blocks = {
        day: group[columns].to_numpy(dtype=float)
        for day, group in residuals.groupby("day", sort=True)
    }
    days = sorted(blocks)
    rows = []
    for position, day in enumerate(days):
        window = days[max(0, position - window_days):position]
        if len(window) < min_days:
            continue
        pooled = np.concatenate([blocks[earlier] for earlier in window])
        if len(pooled) < min_residuals:
            continue
        rows.append(
            {
                "day": day,
                "window_days_used": len(window),
                "observations": int(len(pooled)),
                **{
                    str(level): float(np.nanquantile(pooled[:, index], level))
                    for index, level in enumerate(levels)
                },
            }
        )
    return pd.DataFrame(rows, columns=["day", "window_days_used", "observations", *columns])


def offsets_for_rows(
    frame: pd.DataFrame,
    table: pd.DataFrame,
    levels: Sequence[float],
    published_column: str = "published",
) -> np.ndarray:
    """Each row's offsets, looked up by the day it was published; zero where nothing was known."""
    columns = [str(level) for level in levels]
    if table.empty:
        return np.zeros((len(frame), len(columns)))
    published_day = frame[published_column].dt.floor("D")
    keyed = pd.DataFrame({"day": published_day.to_numpy()}).merge(
        table[["day", *columns]], on="day", how="left"
    )
    return keyed[columns].fillna(0.0).to_numpy(dtype=float)


def calibrate(
    predictions: Mapping[float, Sequence[float]], offsets: np.ndarray
) -> dict[float, np.ndarray]:
    """Shift every level by its own offset, then re-sort so the result is still a distribution."""
    levels = sorted(predictions)
    if offsets.shape[1] != len(levels):
        raise ValueError(f"Expected one offset per level ({len(levels)}); got {offsets.shape[1]}.")
    shifted = {
        level: np.asarray(predictions[level], dtype=float) + offsets[:, index]
        for index, level in enumerate(levels)
    }
    return enforce_non_crossing(shifted)


@dataclass(frozen=True)
class ConformalResult:
    predictions: dict[float, np.ndarray]
    offsets: np.ndarray
    table: pd.DataFrame
    levels: tuple[float, ...]
    window_days: int


def rolling_conformal(
    frame: pd.DataFrame,
    predictions: Mapping[float, Sequence[float]],
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_days: int = MIN_SETTLED_DAYS,
    min_residuals: int = MIN_RESIDUALS,
) -> ConformalResult:
    """The whole layer: residuals, trailing offsets, and the recalibrated forecast."""
    levels = tuple(sorted(predictions))
    residuals = residual_frame(frame, predictions)
    table = rolling_offsets(
        residuals, levels, window_days=window_days, min_days=min_days, min_residuals=min_residuals
    )
    offsets = offsets_for_rows(frame, table, levels)
    return ConformalResult(
        predictions=calibrate(predictions, offsets),
        offsets=offsets,
        table=table,
        levels=levels,
        window_days=window_days,
    )


def offset_summary(result: ConformalResult) -> dict:
    """What the correction actually did, for the write-up.

    A negative mean offset at a low level and a positive one at a high level is the
    signature of a forecast that was too narrow: both ends are being pushed outwards.
    """
    rows = len(result.offsets)
    corrected = int(np.count_nonzero(result.offsets.any(axis=1)))
    return {
        "window_days": result.window_days,
        "days_with_offsets": int(len(result.table)),
        "rows_corrected": corrected,
        "share_corrected": round(corrected / rows, 4) if rows else 0.0,
        "mean_offset": {
            str(level): round(float(result.offsets[:, index].mean()), 4)
            for index, level in enumerate(result.levels)
        },
    }
