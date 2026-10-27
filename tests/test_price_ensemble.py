from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.aemo_constraint_demo import price_ensemble as ens
from src.aemo_constraint_demo import price_metrics as pm

NEM = "+10:00"
LEVELS = (0.1, 0.5, 0.9)


def flat(value: float, rows: int) -> dict[float, np.ndarray]:
    return {level: np.full(rows, value + (level - 0.5) * 20) for level in LEVELS}


def two_week_frame(rows_per_day: int = 48, days: int = 14) -> pd.DataFrame:
    """Evenly spaced intervals over whole days.

    ``published`` is set to the interval itself, which is the strictest case for the
    as-of rule: even a forecast published at the moment the interval settles must not be
    weighted by that day's own outcome.
    """
    minutes = 24 * 60 // rows_per_day
    stamps = pd.date_range("2026-08-01", periods=rows_per_day * days, freq=f"{minutes}min", tz=NEM)
    return pd.DataFrame(
        {
            "published": stamps,
            "target": stamps,
            "actual_rrp": 100.0,
            "regime": "normal",
        }
    )


def test_mean_pinball_by_row_is_one_number_per_row() -> None:
    actual = [100.0, 100.0]
    predictions = {0.1: [90.0, 100.0], 0.5: [100.0, 100.0], 0.9: [110.0, 100.0]}

    losses = ens.mean_pinball_by_row(actual, predictions)

    assert losses.shape == (2,)
    assert losses[1] == 0.0  # the second row is forecast exactly
    assert losses[0] > 0


def test_daily_losses_average_within_a_settled_day() -> None:
    frame = two_week_frame(rows_per_day=2, days=2)
    losses = {"a": np.array([1.0, 3.0, 10.0, 10.0])}

    daily = ens.daily_losses(frame, losses)

    assert list(daily["a"]) == [2.0, 10.0]
    assert list(daily["observations"]) == [2, 2]


def test_recent_losses_never_include_the_row_s_own_day() -> None:
    frame = two_week_frame(rows_per_day=2, days=3)
    # the first day is terrible, the rest are perfect
    losses = {"a": np.array([100.0, 100.0, 0.0, 0.0, 0.0, 0.0])}

    recent = ens.recent_losses(frame, losses, window_days=7)

    assert np.isnan(recent["a"].iloc[0])  # nothing had settled yet on day one
    assert recent["a"].iloc[2] == 100.0  # day two sees only day one
    assert recent["a"].iloc[4] == 50.0  # day three sees days one and two


def test_inverse_loss_weights_favour_the_forecast_that_has_been_right() -> None:
    losses = pd.DataFrame({"good": [1.0, 2.0], "bad": [9.0, 8.0]})

    weights = ens.inverse_loss_weights(losses)

    assert weights["good"].iloc[0] == pytest.approx(0.9)
    assert weights.sum(axis=1).round(6).tolist() == [1.0, 1.0]
    assert (weights["good"] > weights["bad"]).all()


def test_weights_fall_back_to_equal_when_nothing_has_settled() -> None:
    losses = pd.DataFrame({"a": [np.nan], "b": [np.nan]})

    assert ens.inverse_loss_weights(losses).iloc[0].tolist() == [0.5, 0.5]
    assert ens.selection_weights(losses).iloc[0].tolist() == [0.5, 0.5]


def test_selection_weights_put_everything_on_the_recent_leader() -> None:
    losses = pd.DataFrame({"a": [1.0, 5.0], "b": [2.0, 0.5]})

    weights = ens.selection_weights(losses)

    assert weights["a"].tolist() == [1.0, 0.0]
    assert weights["b"].tolist() == [0.0, 1.0]


def test_combine_averages_the_quantile_functions() -> None:
    predictions = {"a": flat(100.0, 2), "b": flat(200.0, 2)}
    weights = pd.DataFrame({"a": [0.25, 1.0], "b": [0.75, 0.0]})

    combined = ens.combine(predictions, weights)

    assert combined[0.5].tolist() == [175.0, 100.0]
    assert combined[0.9].tolist() == [175.0 + 8.0, 100.0 + 8.0]


def test_an_adaptive_ensemble_moves_its_weight_onto_the_better_forecast() -> None:
    frame = two_week_frame()
    rows = len(frame)
    predictions = {"good": flat(100.0, rows), "bad": flat(160.0, rows)}

    result = ens.adaptive_ensemble(frame, predictions, window_days=7)

    first_day = result.weights.iloc[:48]
    last_day = result.weights.iloc[-48:]
    assert first_day["good"].mean() == pytest.approx(0.5)  # nothing to learn from yet
    assert last_day["good"].mean() > 0.75
    assert ens.weight_summary(result.weights)["overall"]["good"] > 0.7


def test_the_ensemble_scores_better_than_the_forecast_it_learns_to_drop() -> None:
    frame = two_week_frame()
    rows = len(frame)
    predictions = {"good": flat(100.0, rows), "bad": flat(160.0, rows)}
    actual = frame["actual_rrp"].to_numpy()

    result = ens.adaptive_ensemble(frame, predictions, mode="select", window_days=7)

    assert pm.crps_from_quantiles(actual, result.predictions) < pm.crps_from_quantiles(
        actual, predictions["bad"]
    )


def test_regime_weights_fall_back_when_a_regime_has_too_little_history() -> None:
    frame = two_week_frame()
    frame.loc[frame.index[:3], "regime"] = "volatile"  # three rows all week: not enough
    rows = len(frame)
    predictions = {"good": flat(100.0, rows), "bad": flat(160.0, rows)}

    result = ens.adaptive_ensemble(frame, predictions, window_days=7, by_regime=True)

    assert result.weights.notna().all().all()
    assert result.weights.sum(axis=1).round(6).unique().tolist() == [1.0]


def test_weight_summary_splits_by_label() -> None:
    weights = pd.DataFrame({"a": [1.0, 0.0, 0.5], "b": [0.0, 1.0, 0.5]})

    summary = ens.weight_summary(weights, labels=["x", "y", "x"])

    assert summary["by_label"]["x"]["a"] == 0.75
    assert summary["by_label"]["y"]["b"] == 1.0


def test_unknown_mode_is_refused() -> None:
    frame = two_week_frame(rows_per_day=2, days=2)
    predictions = {"a": flat(100.0, 4)}

    with pytest.raises(ValueError, match="weight"):
        ens.adaptive_ensemble(frame, predictions, mode="magic")
