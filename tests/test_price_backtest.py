from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts import price_backtest
from src.aemo_constraint_demo import price_model as pmodel

NEM = "+10:00"


def scored_frame() -> pd.DataFrame:
    rows = 8
    frame = pd.DataFrame(
        {
            "actual_rrp": np.linspace(50, 120, rows),
            "horizon_bucket": ["0-2h"] * 4 + ["2-6h"] * 4,
            "regime": ["normal"] * 6 + ["volatile"] * 2,
            "fold": ["2026-08"] * 4 + ["2026-09"] * 4,
        }
    )
    for name in price_backtest.FORECASTS:
        for level in pmodel.QUANTILES:
            offset = (level - 0.5) * 40
            frame[f"{name}_q{int(level * 100):02d}"] = frame["actual_rrp"] + offset
    return frame


def test_read_predictions_collects_every_quantile_column() -> None:
    predictions = price_backtest.read_predictions(scored_frame(), "model")

    assert set(predictions) == set(pmodel.QUANTILES)
    assert len(predictions[0.5]) == 8


def test_compact_score_reports_accuracy_and_calibration_together() -> None:
    frame = scored_frame()
    actual = frame["actual_rrp"].to_numpy()

    score = price_backtest.compact_score(actual, price_backtest.read_predictions(frame, "model"))

    assert score["n"] == 8
    assert score["mae_median"] == 0.0  # the median is exactly right by construction
    assert score["coverage_80"] == 1.0  # the 10-90 band is 32 wide around it
    assert score["width_80"] == pytest.approx(32.0)
    assert score["crps"] > 0  # the other quantiles are not free


def test_score_everything_slices_by_horizon_regime_and_fold() -> None:
    report = price_backtest.score_everything(scored_frame())

    assert set(report["by_horizon"]) == {"0-2h", "2-6h"}
    assert set(report["by_regime"]) == {"normal", "volatile"}
    assert set(report["by_fold"]) == {"2026-08", "2026-09"}
    assert report["by_regime"]["volatile"]["model"]["n"] == 2
    assert set(report["overall"]) == set(price_backtest.FORECASTS)
    assert report["overall"]["model"]["point"]["mae"] == 0.0


def dataset_frame() -> pd.DataFrame:
    """Two months of forecasts with a price that depends on the features."""
    rng = np.random.default_rng(11)
    rows = 1100  # hourly from 1 August, so August trains and September tests
    published = pd.to_datetime(
        pd.date_range("2026-08-01", periods=rows, freq="1h", tz=NEM)
    )
    horizon = rng.choice([30, 90, 300], rows)
    last_rrp = rng.uniform(20, 150, rows)
    actual = last_rrp * 0.6 + rng.normal(0, 8, rows) + 20
    frame = pd.DataFrame(
        {
            "published": published,
            "target": published + pd.to_timedelta(horizon, unit="m"),
            "horizon_min": horizon,
            "actual_rrp": actual,
            "aemo_base": actual + rng.normal(0, 12, rows),
            "last_rrp": last_rrp,
            "last_demand": rng.uniform(5000, 9000, rows),
            "last_vre_share": rng.uniform(0, 0.6, rows),
            "rrp_std_24h": rng.uniform(1, 50, rows),
            "half_hour_of_day": rng.integers(0, 48, rows),
            "day_of_week": rng.integers(0, 7, rows),
            "month": published.month,
        }
    )
    for level in pmodel.QUANTILES:
        frame[f"p{int(level * 100):02d}"] = frame["aemo_base"] + (level - 0.5) * 60
    return frame


def test_run_fold_attaches_four_forecasts_to_the_test_rows_only() -> None:
    dataset = dataset_frame()
    features = ["aemo_base", "horizon_min", "last_rrp", "last_demand", "last_vre_share", "p90"]
    folds = pmodel.monthly_folds(dataset, n_folds=1, min_train_months=1)
    assert folds, "the fixture should span more than one month"

    scored = price_backtest.run_fold(dataset, folds[0], features, max_iter=20)

    assert len(scored) == len(folds[0].test)
    assert scored["published"].min() > dataset["published"].iloc[folds[0].train].max()
    for name in price_backtest.FORECASTS:
        assert f"{name}_q50" in scored
        assert scored[f"{name}_q50"].notna().all()
    assert set(scored["regime"]) <= set(pmodel.REGIMES)
    assert scored["fold"].nunique() == 1


def test_a_fitted_model_beats_a_deliberately_biased_scenario_spread() -> None:
    dataset = dataset_frame()
    features = ["aemo_base", "horizon_min", "last_rrp", "last_demand", "last_vre_share"]
    fold = pmodel.monthly_folds(dataset, n_folds=1, min_train_months=1)[0]

    scored = price_backtest.run_fold(dataset, fold, features, max_iter=60)
    actual = scored["actual_rrp"].to_numpy()

    model = price_backtest.compact_score(actual, price_backtest.read_predictions(scored, "model"))
    scenarios = price_backtest.compact_score(
        actual, price_backtest.read_predictions(scored, "scenarios")
    )
    assert model["crps"] < scenarios["crps"]
