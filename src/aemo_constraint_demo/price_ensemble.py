"""Combine price forecasts adaptively, by how well each has been doing lately.

No single forecast wins everywhere: AEMO's scenarios carry information a model cannot
reconstruct, persistence is hard to beat half an hour out, and a fitted model earns its
keep in the middle horizons. The question this module answers is whether a combination
that changes with market conditions beats any fixed choice.

Two combination rules, both adaptive:

- ``weight``: average the forecasts' quantile functions with weights inversely proportional
  to each one's recent pinball loss. Averaging quantile functions (rather than densities)
  keeps the result a valid, non-crossing forecast and is the standard way to pool
  predictive distributions.
- ``select``: use the single forecast with the lowest recent loss, which is the sharper
  version of the same idea and the one that is easiest to explain to an operator.

"Recent" means **complete days that had already settled when the forecast was published**.
A day's loss is only knowable once its intervals have settled, so a row published on day D
is weighted by days up to D-1 and never by its own outcome. With ``by_regime`` the recent
loss is tracked separately per market regime, so a forecast that is only good in volatile
conditions is only trusted there; a regime with too little recent history falls back to the
overall weights rather than to a number computed from three observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .price_metrics import pinball_loss

MIN_OBSERVATIONS = 48  # a day of half hours; below this a regime's recent loss is noise


def mean_pinball_by_row(
    actual: Sequence[float], predictions: Mapping[float, Sequence[float]]
) -> np.ndarray:
    """Average pinball loss across levels, per row: one number for a whole distribution."""
    levels = sorted(predictions)
    if not levels:
        raise ValueError("No quantile predictions to score.")
    stacked = np.column_stack([pinball_loss(actual, predictions[level], level) for level in levels])
    return stacked.mean(axis=1)


def daily_losses(
    frame: pd.DataFrame,
    losses: Mapping[str, np.ndarray],
    settled_column: str = "target",
    group_column: str | None = None,
) -> pd.DataFrame:
    """Mean loss per forecast per settled day, optionally split by regime."""
    day = frame[settled_column].dt.floor("D")
    table = pd.DataFrame({name: values for name, values in losses.items()})
    table["day"] = day.to_numpy()
    keys = ["day"]
    if group_column is not None:
        table["group"] = frame[group_column].to_numpy()
        keys = ["group", "day"]
    grouped = table.groupby(keys)
    means = grouped.mean()
    means["observations"] = grouped.size()
    return means.reset_index()


def _trailing_mean(
    daily: pd.DataFrame, forecasts: Sequence[str], window_days: int
) -> pd.DataFrame:
    """For each day, the mean loss over the previous ``window_days`` complete days."""
    ordered = daily.sort_values("day")
    rolled = (
        ordered[list(forecasts)]
        .rolling(window_days, min_periods=1)
        .mean()
        .shift(1)  # a day cannot weight itself
    )
    rolled["observations"] = ordered["observations"].rolling(window_days, min_periods=1).sum().shift(1)
    rolled["day"] = ordered["day"].to_numpy()
    return rolled


def recent_losses(
    frame: pd.DataFrame,
    losses: Mapping[str, np.ndarray],
    window_days: int = 7,
    by_regime: bool = False,
    regime_column: str = "regime",
    published_column: str = "published",
) -> pd.DataFrame:
    """Each row's view of how the forecasts had been doing when it was published."""
    forecasts = list(losses)
    overall = _trailing_mean(daily_losses(frame, losses), forecasts, window_days)
    published_day = frame[published_column].dt.floor("D")
    joined = pd.DataFrame({"day": published_day.to_numpy()}).merge(overall, on="day", how="left")

    if not by_regime:
        return joined[forecasts].reset_index(drop=True)

    per_regime = daily_losses(frame, losses, group_column=regime_column)
    pieces = []
    for regime, group in per_regime.groupby("group"):
        rolled = _trailing_mean(group, forecasts, window_days)
        rolled["group"] = regime
        pieces.append(rolled)
    regime_table = pd.concat(pieces, ignore_index=True)
    keyed = pd.DataFrame(
        {"day": published_day.to_numpy(), "group": frame[regime_column].to_numpy()}
    ).merge(regime_table, on=["day", "group"], how="left")

    thin = (keyed["observations"].isna() | (keyed["observations"] < MIN_OBSERVATIONS)).to_numpy()
    out = keyed[forecasts].copy()
    out.loc[thin, :] = joined.loc[thin, forecasts].to_numpy()
    return out.reset_index(drop=True)


def inverse_loss_weights(losses: pd.DataFrame, floor: float = 1e-3) -> pd.DataFrame:
    """Weights inversely proportional to recent loss; equal weights where nothing is known."""
    values = losses.to_numpy(dtype=float)
    known = ~np.isnan(values).all(axis=1)
    safe = values.copy()
    if known.any():
        # A forecast with no recent history is given its row's worst loss, not a free pass.
        row_worst = np.nanmax(values[known], axis=1, keepdims=True)
        safe[known] = np.where(np.isnan(values[known]), row_worst, values[known])
    safe = np.maximum(np.where(np.isnan(safe), floor, safe), floor)
    weights = 1.0 / safe
    weights = weights / weights.sum(axis=1, keepdims=True)
    weights[~known] = 1.0 / values.shape[1]
    return pd.DataFrame(weights, columns=losses.columns, index=losses.index)


def selection_weights(losses: pd.DataFrame) -> pd.DataFrame:
    """All the weight on the forecast with the lowest recent loss."""
    values = losses.to_numpy(dtype=float)
    known = ~np.isnan(values).all(axis=1)
    filled = np.where(np.isnan(values), np.inf, values)
    weights = np.zeros_like(values)
    weights[np.arange(len(values)), np.argmin(filled, axis=1)] = 1.0
    weights[~known] = 1.0 / values.shape[1]
    return pd.DataFrame(weights, columns=losses.columns, index=losses.index)


def combine(
    predictions: Mapping[str, Mapping[float, np.ndarray]], weights: pd.DataFrame
) -> dict[float, np.ndarray]:
    """Weighted average of quantile functions, level by level."""
    names = list(weights.columns)
    levels = sorted(next(iter(predictions.values())))
    combined = {}
    for level in levels:
        stacked = np.column_stack([np.asarray(predictions[name][level], dtype=float) for name in names])
        combined[level] = (stacked * weights.to_numpy(dtype=float)).sum(axis=1)
    return combined


@dataclass(frozen=True)
class EnsembleResult:
    predictions: dict[float, np.ndarray]
    weights: pd.DataFrame
    recent: pd.DataFrame


def adaptive_ensemble(
    frame: pd.DataFrame,
    predictions: Mapping[str, Mapping[float, np.ndarray]],
    mode: str = "weight",
    window_days: int = 7,
    by_regime: bool = False,
) -> EnsembleResult:
    actual = frame["actual_rrp"].to_numpy(dtype=float)
    losses = {name: mean_pinball_by_row(actual, quantiles) for name, quantiles in predictions.items()}
    recent = recent_losses(frame, losses, window_days=window_days, by_regime=by_regime)
    if mode == "weight":
        weights = inverse_loss_weights(recent)
    elif mode == "select":
        weights = selection_weights(recent)
    else:
        raise ValueError(f"Unknown ensemble mode {mode!r}; use 'weight' or 'select'.")
    return EnsembleResult(predictions=combine(predictions, weights), weights=weights, recent=recent)


def weight_summary(weights: pd.DataFrame, labels: Iterable[str] | None = None) -> dict:
    """Average weight per forecast, overall and per label, for the write-up."""
    summary: dict = {"overall": {name: round(float(weights[name].mean()), 4) for name in weights.columns}}
    if labels is not None:
        frame = weights.copy()
        frame["label"] = list(labels)
        summary["by_label"] = {
            str(label): {name: round(float(group[name].mean()), 4) for name in weights.columns}
            for label, group in frame.groupby("label")
        }
    return summary
