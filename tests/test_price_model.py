from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.aemo_constraint_demo import price_model as pmodel

NEM = "+10:00"


def frame_with_months() -> pd.DataFrame:
    published = pd.to_datetime(
        [f"2026-0{month}-15 10:00{NEM}" for month in (1, 2, 3, 4, 5)] * 2
    )
    published = pd.Series(sorted(published))
    return pd.DataFrame(
        {"published": published, "target": published + pd.Timedelta("1h"), "value": range(10)}
    )


def test_monthly_folds_purge_training_rows_that_land_in_the_test_month() -> None:
    """A forecast published in March for an April interval cannot train the April fold."""
    published = pd.to_datetime(
        [f"2026-03-31 23:{minute:02d}{NEM}" for minute in (0, 30)] + [f"2026-04-02 10:00{NEM}"]
    )
    frame = pd.DataFrame(
        {
            "published": published,
            # the first March row forecasts an April interval; the second stays in March
            "target": pd.to_datetime(
                [f"2026-04-01 12:00{NEM}", f"2026-03-31 23:30{NEM}", f"2026-04-02 12:00{NEM}"]
            ),
        }
    )

    fold = pmodel.monthly_folds(frame, n_folds=1, min_train_months=1)[0]

    assert fold.label == "2026-04"
    assert list(fold.train) == [1]  # the March row whose interval is also in March
    assert list(fold.test) == [2]


def test_purging_can_be_turned_off() -> None:
    published = pd.to_datetime([f"2026-03-31 23:00{NEM}", f"2026-04-02 10:00{NEM}"])
    frame = pd.DataFrame(
        {
            "published": published,
            "target": pd.to_datetime([f"2026-04-01 12:00{NEM}", f"2026-04-02 12:00{NEM}"]),
        }
    )

    fold = pmodel.monthly_folds(frame, n_folds=1, min_train_months=1, target_column=None)[0]

    assert list(fold.train) == [0]


def test_monthly_folds_train_only_on_earlier_months() -> None:
    frame = frame_with_months()

    folds = pmodel.monthly_folds(frame, n_folds=2, min_train_months=2)

    assert [fold.label for fold in folds] == ["2026-04", "2026-05"]
    for fold in folds:
        latest_train = frame["published"].iloc[fold.train].max()
        earliest_test = frame["published"].iloc[fold.test].min()
        assert latest_train < earliest_test


def test_monthly_folds_need_a_minimum_history() -> None:
    frame = frame_with_months()

    folds = pmodel.monthly_folds(frame, n_folds=10, min_train_months=4)

    assert [fold.label for fold in folds] == ["2026-05"]


def regime_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "last_vre_share": [0.1, 0.9, 0.1, 0.1, 0.9],
            "last_demand": [5000.0, 5000.0, 9000.0, 5000.0, 9000.0],
            "rrp_std_24h": [5.0, 5.0, 5.0, 200.0, 200.0],
        }
    )


def test_regime_labels_put_the_hardest_condition_first() -> None:
    frame = regime_frame()
    thresholds = pmodel.RegimeThresholds(vre_share=0.5, demand=8000.0, volatility=100.0)

    labels = list(pmodel.regime_labels(frame, thresholds))

    assert labels == ["normal", "high_vre", "peak_demand", "volatile", "volatile"]


def test_regime_thresholds_come_from_the_training_fold() -> None:
    train = pd.DataFrame(
        {
            "last_vre_share": [0.0, 0.2, 0.4, 0.6],
            "last_demand": [1000.0, 2000.0, 3000.0, 4000.0],
            "rrp_std_24h": [1.0, 2.0, 3.0, 4.0],
        }
    )

    thresholds = pmodel.fit_regime_thresholds(train, quantile=0.75)

    assert thresholds.vre_share == pytest.approx(0.45)
    assert thresholds.demand == pytest.approx(3250.0)


def test_horizon_buckets_cover_the_published_range() -> None:
    horizons = pd.Series([30, 120, 121, 700, 1439, 1441, 2363])

    assert list(pmodel.horizon_bucket(horizons)) == [
        "0-2h", "0-2h", "2-6h", "6-12h", "12-24h", "24h+", "24h+"
    ]


def test_enforce_non_crossing_sorts_each_row() -> None:
    crossed = {0.1: np.array([50.0, 10.0]), 0.5: np.array([40.0, 20.0]), 0.9: np.array([30.0, 30.0])}

    fixed = pmodel.enforce_non_crossing(crossed)

    assert list(fixed[0.1]) == [30.0, 10.0]
    assert list(fixed[0.9]) == [50.0, 30.0]
    for low, high in ((0.1, 0.5), (0.5, 0.9)):
        assert np.all(fixed[low] <= fixed[high])


def scenario_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "p05": [10.0], "p10": [20.0], "p25": [30.0], "p50": [40.0],
            "p75": [60.0], "p90": [80.0], "p95": [90.0],
            "aemo_base": [100.0],
        }
    )


def test_scenario_quantiles_read_the_published_spread() -> None:
    quantiles = pmodel.scenario_quantiles(scenario_frame())

    assert quantiles[0.5][0] == 40.0
    assert quantiles[0.95][0] == 90.0


def test_recentring_moves_the_spread_onto_the_base_scenario() -> None:
    frame = scenario_frame()

    recentred = pmodel.recentred_scenario_quantiles(frame)

    assert recentred[0.5][0] == 100.0  # the base scenario is now the median
    # the shape is unchanged: every gap is preserved
    assert recentred[0.9][0] - recentred[0.5][0] == 80.0 - 40.0
    assert recentred[0.05][0] == 10.0 + 60.0


def test_scenario_quantiles_say_what_is_missing() -> None:
    with pytest.raises(KeyError, match="p05"):
        pmodel.scenario_quantiles(pd.DataFrame({"p50": [1.0]}), levels=(0.05, 0.5))


def test_persistence_widens_by_its_own_training_error_per_horizon() -> None:
    train = pd.DataFrame(
        {
            "horizon_min": [30] * 5 + [1500] * 5,
            "last_rrp": [100.0] * 10,
            # half-hour ahead persistence is nearly right; a day ahead it is not
            "actual_rrp": [99.0, 100.0, 101.0, 100.0, 100.0, 50.0, 80.0, 100.0, 150.0, 200.0],
        }
    )
    test = pd.DataFrame({"horizon_min": [30, 1500], "last_rrp": [70.0, 70.0]})

    quantiles = pmodel.persistence_quantiles(train, test, levels=(0.1, 0.5, 0.9))

    assert quantiles[0.5][0] == pytest.approx(70.0)  # median training error is 0 at 30 min
    short_width = quantiles[0.9][0] - quantiles[0.1][0]
    long_width = quantiles[0.9][1] - quantiles[0.1][1]
    assert long_width > short_width * 10


def test_fitted_quantiles_bracket_the_data_in_the_right_order() -> None:
    rng = np.random.default_rng(3)
    features = pd.DataFrame({"x": rng.uniform(0, 10, 600)})
    target = pd.Series(features["x"] * 5 + rng.normal(0, 10, 600))

    models = pmodel.fit_quantile_models(features, target, levels=(0.1, 0.5, 0.9), max_iter=40)
    predictions = pmodel.predict_quantiles(models, features)

    assert np.all(predictions[0.1] <= predictions[0.9])
    below_10 = float(np.mean(target.to_numpy() <= predictions[0.1]))
    below_90 = float(np.mean(target.to_numpy() <= predictions[0.9]))
    assert 0.03 < below_10 < 0.2
    assert 0.8 < below_90 < 0.97


def test_slice_predictions_keeps_levels_aligned() -> None:
    predictions = {0.1: np.arange(5.0), 0.9: np.arange(5.0) + 10}
    mask = np.array([True, False, True, False, True])

    sliced = pmodel.slice_predictions(predictions, mask)

    assert list(sliced[0.1]) == [0.0, 2.0, 4.0]
    assert list(sliced[0.9]) == [10.0, 12.0, 14.0]
