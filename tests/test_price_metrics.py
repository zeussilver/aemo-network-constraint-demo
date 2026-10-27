from __future__ import annotations

import numpy as np
import pytest

from src.aemo_constraint_demo import price_metrics as pm


def test_pinball_loss_punishes_the_side_the_quantile_cares_about() -> None:
    # at the 90th percentile, forecasting too low costs nine times as much as too high
    under = pm.pinball_loss([100.0], [90.0], 0.9)[0]
    over = pm.pinball_loss([100.0], [110.0], 0.9)[0]

    assert under == pytest.approx(0.9 * 10)
    assert over == pytest.approx(0.1 * 10)
    assert under == pytest.approx(9 * over)


def test_pinball_loss_is_symmetric_at_the_median() -> None:
    assert pm.pinball_loss([100.0], [90.0], 0.5)[0] == pytest.approx(
        pm.pinball_loss([100.0], [110.0], 0.5)[0]
    )


def test_pinball_loss_rejects_a_level_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError):
        pm.pinball_loss([1.0], [1.0], 1.0)


def test_a_perfect_forecast_scores_zero() -> None:
    actual = [10.0, 20.0, 30.0]
    predictions = {0.1: actual, 0.5: actual, 0.9: actual}

    assert pm.crps_from_quantiles(actual, predictions) == 0.0
    assert pm.point_metrics(actual, actual)["mae"] == 0.0


def test_crps_is_twice_the_mean_pinball_loss() -> None:
    actual = [10.0, 50.0, 90.0]
    predictions = {0.25: [20.0, 40.0, 60.0], 0.75: [30.0, 60.0, 120.0]}

    scores = pm.quantile_scores(actual, predictions)

    assert pm.crps_from_quantiles(actual, predictions) == pytest.approx(
        2 * np.mean(list(scores.values()))
    )


def test_a_wider_forecast_is_better_calibrated_but_not_automatically_better_scored() -> None:
    rng = np.random.default_rng(7)
    actual = rng.normal(100, 20, 4000)
    tight = {0.1: np.full(4000, 97.0), 0.9: np.full(4000, 103.0)}
    honest = {0.1: np.full(4000, 100 - 1.2816 * 20), 0.9: np.full(4000, 100 + 1.2816 * 20)}

    assert pm.interval_coverage(actual, tight[0.1], tight[0.9]) < 0.3
    assert pm.interval_coverage(actual, honest[0.1], honest[0.9]) == pytest.approx(0.8, abs=0.03)
    assert pm.crps_from_quantiles(actual, honest) < pm.crps_from_quantiles(actual, tight)


def test_reliability_table_reports_the_gap_between_claimed_and_observed() -> None:
    actual = list(range(100))  # 0..99
    # a quantile set at 50 should be exceeded by half the sample
    predictions = {0.5: [50.0] * 100, 0.9: [95.0] * 100}

    table = {row["level"]: row for row in pm.reliability_table(actual, predictions)}

    assert table[0.5]["empirical"] == pytest.approx(0.51)  # values 0..50 inclusive
    assert table[0.9]["gap"] == pytest.approx(0.06, abs=1e-6)  # 96 of 100 below, claimed 90


def test_central_intervals_pair_levels_that_match() -> None:
    actual = [50.0] * 10
    predictions = {0.1: [40.0] * 10, 0.25: [45.0] * 10, 0.75: [55.0] * 10, 0.9: [60.0] * 10}

    rows = {row["nominal"]: row for row in pm.central_intervals(actual, predictions)}

    assert set(rows) == {0.8, 0.5}
    assert rows[0.8]["coverage"] == 1.0
    assert rows[0.8]["mean_width"] == 20.0
    assert rows[0.5]["mean_width"] == 10.0


def test_point_metrics_separate_the_mean_from_the_median_error() -> None:
    actual = [10.0, 10.0, 10.0, 10.0]
    predicted = [11.0, 11.0, 11.0, 1000.0]  # one spike the model missed badly

    metrics = pm.point_metrics(actual, predicted)

    assert metrics["median_abs_error"] == 1.0
    assert metrics["mae"] > 100
    assert metrics["bias"] > 0  # over-forecast on average
    assert metrics["n"] == 4


def test_score_forecast_returns_every_view(tmp_path) -> None:
    actual = [10.0, 20.0, 30.0, 40.0]
    predictions = {
        0.1: [5.0, 15.0, 25.0, 35.0],
        0.5: [10.0, 20.0, 30.0, 45.0],
        0.9: [20.0, 30.0, 40.0, 60.0],
    }

    report = pm.score_forecast(actual, predictions)

    assert report["point"]["n"] == 4
    assert set(report["pinball_by_level"]) == {"0.1", "0.5", "0.9"}
    assert [row["nominal"] for row in report["intervals"]] == [0.8]
    assert report["crps"] > 0


def test_nan_rows_do_not_poison_a_score() -> None:
    actual = [10.0, np.nan, 30.0]
    predictions = {0.5: [10.0, 20.0, 30.0]}

    assert pm.crps_from_quantiles(actual, predictions) == 0.0
