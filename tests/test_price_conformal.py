from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.aemo_constraint_demo import price_conformal as conf
from src.aemo_constraint_demo import price_metrics as pm

NEM = "+10:00"
LEVELS = (0.1, 0.5, 0.9)
Z = {0.1: -1.2816, 0.5: 0.0, 0.9: 1.2816}  # standard normal quantiles at those levels


def narrow_frame(
    rows_per_day: int = 48,
    days: int = 40,
    true_sd: float = 20.0,
    claimed_sd: float = 5.0,
    seed: int = 11,
) -> tuple[pd.DataFrame, dict[float, np.ndarray]]:
    """A forecast of a known spread that claims a different one, over enough days for a window.

    ``published`` is the interval itself, the strictest case for the as-of rule: even a
    forecast published as the interval settles must not be corrected by that day's outcome.
    """
    minutes = 24 * 60 // rows_per_day
    stamps = pd.date_range("2026-07-01", periods=rows_per_day * days, freq=f"{minutes}min", tz=NEM)
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        {
            "published": stamps,
            "target": stamps,
            "actual_rrp": 100.0 + rng.normal(0.0, true_sd, len(stamps)),
        }
    )
    predictions = {level: np.full(len(stamps), 100.0 + Z[level] * claimed_sd) for level in LEVELS}
    return frame, predictions


def test_residuals_measure_the_distance_from_each_quantile() -> None:
    stamps = pd.date_range("2026-08-01", periods=2, freq="D", tz=NEM)
    frame = pd.DataFrame({"published": stamps, "target": stamps, "actual_rrp": [100.0, 50.0]})
    predictions = {0.1: np.array([90.0, 90.0]), 0.9: np.array([110.0, 110.0])}

    residuals = conf.residual_frame(frame, predictions)

    assert residuals["0.1"].tolist() == [10.0, -40.0]
    assert residuals["0.9"].tolist() == [-10.0, -60.0]


def test_a_day_is_never_corrected_by_its_own_outcome() -> None:
    """The one property the whole layer rests on: no offset may see the day it applies to."""
    days = pd.date_range("2026-08-01", periods=5, freq="D", tz=NEM)
    # four quiet days of one row each, then a loud day of twenty: including the loud day's
    # own residuals would drag its median from 0 to 100.
    residuals = pd.DataFrame(
        {
            "day": np.concatenate([days[:4].to_numpy(), np.repeat(days[4:].to_numpy(), 20)]),
            "0.5": np.concatenate([np.zeros(4), np.full(20, 100.0)]),
        }
    )

    table = conf.rolling_offsets(residuals, (0.5,), window_days=7, min_days=3, min_residuals=1)

    assert table["0.5"].tolist() == [0.0, 0.0]  # the fourth and fifth days, both read from quiet ones
    assert table["day"].tolist() == [days[3], days[4]]


def test_a_window_has_to_fill_before_it_says_anything() -> None:
    frame, predictions = narrow_frame(rows_per_day=4, days=4)

    table = conf.rolling_offsets(
        conf.residual_frame(frame, predictions), LEVELS, window_days=28
    )

    assert table.empty  # sixteen residuals is not a calibration set


def test_offsets_are_looked_up_by_the_day_the_forecast_was_published() -> None:
    """A forecast published today is corrected from yesterday, not from the day it is for."""
    days = pd.date_range("2026-08-01", periods=3, freq="D", tz=NEM)
    table = pd.DataFrame({"day": days.to_numpy(), "0.5": [1.0, 2.0, 3.0]})
    frame = pd.DataFrame({"published": days[:2], "target": days[1:]})

    offsets = conf.offsets_for_rows(frame, table, (0.5,))

    assert offsets[:, 0].tolist() == [1.0, 2.0]


def test_rows_with_no_offset_keep_the_forecast_they_had() -> None:
    frame, predictions = narrow_frame(rows_per_day=4, days=4)

    result = conf.rolling_conformal(frame, predictions, window_days=28)

    assert not result.offsets.any()
    for level, values in predictions.items():
        assert result.predictions[level].tolist() == values.tolist()


def test_calibration_cannot_leave_the_quantiles_crossed() -> None:
    predictions = {0.1: np.array([90.0]), 0.5: np.array([100.0]), 0.9: np.array([110.0])}
    offsets = np.array([[30.0, 0.0, -30.0]])  # a correction that would invert the order

    calibrated = conf.calibrate(predictions, offsets)

    assert calibrated[0.1][0] == 80.0
    assert calibrated[0.5][0] == 100.0
    assert calibrated[0.9][0] == 120.0


def test_an_offset_block_of_the_wrong_shape_is_refused() -> None:
    predictions = {0.1: np.array([90.0]), 0.9: np.array([110.0])}

    with pytest.raises(ValueError, match="one offset per level"):
        conf.calibrate(predictions, np.zeros((1, 3)))


def test_a_forecast_that_is_too_sure_of_itself_is_widened() -> None:
    frame, predictions = narrow_frame()

    result = conf.rolling_conformal(frame, predictions, window_days=28, min_residuals=100)

    corrected = result.offsets.any(axis=1)
    actual = frame["actual_rrp"].to_numpy()[corrected]
    before = pm.interval_coverage(actual, predictions[0.1][corrected], predictions[0.9][corrected])
    after = pm.interval_coverage(
        actual, result.predictions[0.1][corrected], result.predictions[0.9][corrected]
    )

    assert before < 0.4  # a forecast four times too narrow covers nothing like 80%
    assert 0.7 < after < 0.9


def test_widening_an_over_confident_forecast_also_scores_better() -> None:
    """Coverage bought by widening a bad forecast still has to not cost CRPS."""
    frame, predictions = narrow_frame()

    result = conf.rolling_conformal(frame, predictions, window_days=28, min_residuals=100)

    corrected = result.offsets.any(axis=1)
    actual = frame["actual_rrp"].to_numpy()[corrected]
    sliced = lambda source: {level: values[corrected] for level, values in source.items()}
    assert pm.crps_from_quantiles(actual, sliced(result.predictions)) < pm.crps_from_quantiles(
        actual, sliced(predictions)
    )


def test_a_forecast_that_is_already_honest_is_left_alone() -> None:
    frame, predictions = narrow_frame(claimed_sd=20.0)

    result = conf.rolling_conformal(frame, predictions, window_days=28, min_residuals=100)

    summary = conf.offset_summary(result)
    assert abs(summary["mean_offset"]["0.9"]) < 2.0
    assert abs(summary["mean_offset"]["0.1"]) < 2.0
    corrected = result.offsets.any(axis=1)
    after = pm.interval_coverage(
        frame["actual_rrp"].to_numpy()[corrected],
        result.predictions[0.1][corrected],
        result.predictions[0.9][corrected],
    )
    assert 0.74 < after < 0.86


def test_the_summary_says_which_way_each_level_moved() -> None:
    frame, predictions = narrow_frame()

    summary = conf.offset_summary(
        conf.rolling_conformal(frame, predictions, window_days=28, min_residuals=100)
    )

    # a forecast that was too narrow is pushed outwards at both ends
    assert summary["mean_offset"]["0.1"] < 0
    assert summary["mean_offset"]["0.9"] > 0
    assert summary["window_days"] == 28
    assert 0 < summary["share_corrected"] < 1  # the warm-up rows are never corrected
    assert summary["rows_corrected"] == int(summary["share_corrected"] * len(frame))
