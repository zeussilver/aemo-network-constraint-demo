from __future__ import annotations

import pandas as pd

from scripts.backtest_horizon_model import (
    ValidationWindow,
    evaluate_event_windows,
    find_positive_event_windows,
    precision_and_lift_at_top_fraction,
)
from src.aemo_constraint_demo.features import add_time_features, select_feature_columns


def test_event_window_backtest_uses_only_prior_training_rows() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=480, freq="5min")
    target = [0] * len(intervals)
    target[12] = 1
    target[360] = 1
    frame = pd.DataFrame(
        {
            "SETTLEMENTDATE": intervals,
            "feature": range(len(intervals)),
            "target_constrained_next_6h": target,
        }
    )
    windows = find_positive_event_windows(frame, "target_constrained_next_6h", window_hours=24, max_folds=1)

    records = evaluate_event_windows(
        frame=frame,
        feature_cols=["feature"],
        target_column="target_constrained_next_6h",
        windows=windows,
    )

    assert len(records) == 1
    assert records[0]["skipped"] is False
    assert pd.Timestamp(records[0]["train_end"]) < pd.Timestamp(records[0]["validation_start"])


def test_event_window_backtest_reports_skipped_fold_for_one_class_training() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=480, freq="5min")
    target = [0] * len(intervals)
    target[360] = 1
    frame = pd.DataFrame(
        {
            "SETTLEMENTDATE": intervals,
            "feature": range(len(intervals)),
            "target_constrained_next_6h": target,
        }
    )
    windows = find_positive_event_windows(frame, "target_constrained_next_6h", window_hours=24, max_folds=1)

    records = evaluate_event_windows(
        frame=frame,
        feature_cols=["feature"],
        target_column="target_constrained_next_6h",
        windows=windows,
    )

    assert records[0]["skipped"] is True
    assert records[0]["skip_reason"] == "prior_training_has_one_target_class"


def test_event_window_backtest_reports_skipped_fold_for_one_class_validation() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=40, freq="5min")
    target = [0] * 40
    target[3] = 1
    frame = pd.DataFrame(
        {
            "SETTLEMENTDATE": intervals,
            "feature": range(len(intervals)),
            "target_constrained_next_6h": target,
        }
    )

    records = evaluate_event_windows(
        frame=frame,
        feature_cols=["feature"],
        target_column="target_constrained_next_6h",
        windows=[ValidationWindow(validation_start=intervals[10], validation_end=intervals[20])],
    )

    assert records[0]["skipped"] is True
    assert records[0]["skip_reason"] == "validation_has_one_target_class"


def test_lift_at_top_fraction_handles_zero_positive_validation_safely() -> None:
    precision, lift = precision_and_lift_at_top_fraction([0, 0, 0, 0], [0.9, 0.4, 0.2, 0.1], 0.05)

    assert precision == 0.0
    assert lift is None


def test_select_feature_columns_excludes_target_and_horizon_label_columns() -> None:
    frame = add_time_features(pd.DataFrame({"SETTLEMENTDATE": ["2026-05-01 08:00:00"]}))
    frame["temperature_2m"] = 18.0
    frame["TOTALCLEARED"] = 10.0
    frame["target_constrained"] = 0
    frame["target_constrained_next_6h"] = 1

    columns = select_feature_columns(frame, include_dispatch_features=True)

    assert "hour_sin" in columns
    assert "temperature_2m" in columns
    assert "TOTALCLEARED" in columns
    assert "target_constrained" not in columns
    assert "target_constrained_next_6h" not in columns
