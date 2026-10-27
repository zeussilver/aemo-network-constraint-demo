"""Scoring rules for probabilistic price forecasts.

A point forecast can be scored with an absolute error; a forecast that claims "the price
will be between $40 and $120 four times in five" has to be scored on two things at once:
whether the middle of the distribution is in the right place (**sharpness**) and whether
the stated probabilities are true (**calibration**). A forecast can be sharp and wrong, or
honest and useless, and only one of those is fixable by widening the interval.

- ``pinball_loss`` is the scoring rule for a single quantile, and the thing a quantile
  model is trained to minimise. Under-forecasting the 90th percentile is penalised nine
  times as heavily as over-forecasting it, which is what makes the forecast a quantile.
- ``crps_from_quantiles`` turns the whole set of quantiles into one number, using the
  identity that the CRPS is twice the integral of the pinball loss over all levels. With
  quantiles on a grid it is an approximation, and gets better as the grid gets finer.
- ``reliability_table`` is the calibration check: of the intervals that a forecast said the
  price would fall below 90% of the time, did it, 90% of the time?
- ``interval_coverage`` is the same question asked of a central interval, the form a trader
  actually reads.

Every function takes plain arrays, so a baseline that was never fitted (AEMO's own
scenarios, say) is scored exactly like a model that was.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np


def pinball_loss(actual: Sequence[float], predicted: Sequence[float], level: float) -> np.ndarray:
    """Per-observation pinball loss at one quantile level."""
    if not 0 < level < 1:
        raise ValueError(f"A quantile level must be in (0, 1); got {level}.")
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = actual - predicted
    return np.where(error >= 0, level * error, (level - 1) * error)


def mean_pinball_loss(actual: Sequence[float], predicted: Sequence[float], level: float) -> float:
    return float(np.nanmean(pinball_loss(actual, predicted, level)))


def quantile_scores(
    actual: Sequence[float], predictions: Mapping[float, Sequence[float]]
) -> dict[float, float]:
    """Mean pinball loss for each level the forecast provides."""
    return {level: mean_pinball_loss(actual, values, level) for level, values in sorted(predictions.items())}


def crps_from_quantiles(
    actual: Sequence[float], predictions: Mapping[float, Sequence[float]]
) -> float:
    """CRPS approximated from a grid of quantiles: twice the mean pinball loss.

    Exact as the grid becomes dense; with the usual 5th-to-95th grid it is a consistent
    comparison between forecasts scored on the same grid, not an absolute CRPS.
    """
    scores = quantile_scores(actual, predictions)
    if not scores:
        raise ValueError("No quantile predictions to score.")
    return float(2 * np.mean(list(scores.values())))


def interval_coverage(
    actual: Sequence[float], lower: Sequence[float], upper: Sequence[float]
) -> float:
    """Share of actuals that landed inside the interval."""
    actual = np.asarray(actual, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    inside = (actual >= lower) & (actual <= upper)
    return float(np.nanmean(inside.astype(float)))


def interval_width(lower: Sequence[float], upper: Sequence[float]) -> float:
    return float(np.nanmean(np.asarray(upper, dtype=float) - np.asarray(lower, dtype=float)))


def reliability_table(
    actual: Sequence[float], predictions: Mapping[float, Sequence[float]]
) -> list[dict[str, float]]:
    """For each level: how often the price actually fell below the quantile claimed.

    ``gap`` is empirical minus nominal, so a positive gap means the quantile sits too high
    and the forecast is wider than it needs to be on that side.
    """
    actual = np.asarray(actual, dtype=float)
    rows = []
    for level, values in sorted(predictions.items()):
        values = np.asarray(values, dtype=float)
        empirical = float(np.nanmean((actual <= values).astype(float)))
        rows.append(
            {
                "level": level,
                "nominal": level,
                "empirical": round(empirical, 5),
                "gap": round(empirical - level, 5),
                "pinball": round(mean_pinball_loss(actual, values, level), 5),
            }
        )
    return rows


def central_intervals(
    actual: Sequence[float], predictions: Mapping[float, Sequence[float]]
) -> list[dict[str, float]]:
    """Coverage and width of each central interval the quantile grid can form."""
    levels = sorted(predictions)
    rows = []
    for low in levels:
        high = round(1 - low, 10)
        if high <= low or high not in predictions:
            continue
        nominal = high - low
        rows.append(
            {
                "nominal": round(nominal, 5),
                "coverage": round(interval_coverage(actual, predictions[low], predictions[high]), 5),
                "mean_width": round(interval_width(predictions[low], predictions[high]), 4),
            }
        )
    return rows


def point_metrics(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, float]:
    """Errors of a point forecast, including the median, which price spikes do not dominate."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = predicted - actual
    return {
        "mae": round(float(np.nanmean(np.abs(error))), 4),
        "median_abs_error": round(float(np.nanmedian(np.abs(error))), 4),
        "rmse": round(float(np.sqrt(np.nanmean(error**2))), 4),
        "bias": round(float(np.nanmean(error)), 4),
        "n": int(np.sum(~np.isnan(actual) & ~np.isnan(predicted))),
    }


def score_forecast(
    actual: Sequence[float], predictions: Mapping[float, Sequence[float]], median_level: float = 0.5
) -> dict:
    """Everything above, for one forecast on one set of rows."""
    median = predictions.get(median_level)
    return {
        "point": point_metrics(actual, median) if median is not None else None,
        "crps": round(crps_from_quantiles(actual, predictions), 5),
        "pinball_by_level": {
            str(level): round(score, 5) for level, score in quantile_scores(actual, predictions).items()
        },
        "reliability": reliability_table(actual, predictions),
        "intervals": central_intervals(actual, predictions),
    }
