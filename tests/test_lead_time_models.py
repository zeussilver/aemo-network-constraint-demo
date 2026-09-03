from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from scripts import train_lead_time_models
from src.aemo_constraint_demo import frames as frames_module
from src.aemo_constraint_demo.features import add_time_features

PERIODS = 24 * 12 * 4  # four days of 5-minute intervals


def _labelled_frame() -> pd.DataFrame:
    timestamps = pd.date_range("2026-05-01 04:05:00", periods=PERIODS, freq="5min")
    frame = add_time_features(pd.DataFrame({"SETTLEMENTDATE": timestamps}))
    frame["temperature_2m"] = np.linspace(5.0, 30.0, PERIODS)
    frame["target_constrained_lead_1h"] = (frame.index % 97 == 0).astype(int)
    return frame


def _pipeline() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=200)),
        ]
    )


def test_fit_target_model_reports_the_training_distribution() -> None:
    frame = _labelled_frame()

    model, stats = train_lead_time_models.fit_target_model(
        frame, ["hour_sin", "temperature_2m"], "target_constrained_lead_1h", "logistic_regression"
    )

    assert hasattr(model, "predict_proba")
    assert stats["target_column"] == "target_constrained_lead_1h"
    assert stats["train_samples"] == PERIODS
    assert stats["positive_samples"] == int(frame["target_constrained_lead_1h"].sum())
    assert 0 < stats["positive_rate"] < 1


def test_fit_target_model_ignores_rows_whose_future_is_unobserved() -> None:
    frame = _labelled_frame()
    frame.loc[frame.tail(12).index, "target_constrained_lead_1h"] = pd.NA

    _, stats = train_lead_time_models.fit_target_model(
        frame, ["hour_sin"], "target_constrained_lead_1h", "logistic_regression"
    )

    assert stats["train_samples"] == PERIODS - 12


def test_evaluate_target_uses_the_shared_event_window_backtest() -> None:
    frame = _labelled_frame()

    summary, records = train_lead_time_models.evaluate_target(
        frame,
        feature_cols=["hour_sin", "hour_cos", "temperature_2m"],
        target_column="target_constrained_lead_1h",
        duid="D1",
        days=4,
        horizon_hours=6,
        model_type="logistic_regression",
        window_hours=24,
        max_folds=3,
    )

    assert summary["target_column"] == "target_constrained_lead_1h"
    assert len(records) <= 3
    assert summary["evaluated_folds"] + summary["skipped_folds"] == len(records)


def test_run_rejects_leads_outside_the_horizon(tmp_path: Path) -> None:
    args = train_lead_time_models.build_arg_parser().parse_args(
        ["--duid", "D1", "--horizon-hours", "6", "--lead-hours", "1", "8"]
    )

    with pytest.raises(ValueError, match="must fall inside"):
        train_lead_time_models.run(args)


def test_run_writes_a_bundle_metrics_folds_and_report(tmp_path: Path, monkeypatch) -> None:
    frame = _labelled_frame()
    frame["target_constrained_lead_2h"] = (frame.index % 89 == 0).astype(int)
    frame["target_constrained_next_2h"] = frame[
        ["target_constrained_lead_1h", "target_constrained_lead_2h"]
    ].max(axis=1)

    model_frame = frames_module.ModelFrame(
        frame=frame,
        feature_columns=["hour_sin", "hour_cos", "temperature_2m"],
        target_column="target_constrained_next_2h",
        connection_point="CP1",
        relevant_genconids=["C1"],
        lead_target_columns={1: "target_constrained_lead_1h", 2: "target_constrained_lead_2h"},
        lhs_rows=pd.DataFrame(),
        metadata={"dropped_incomplete_horizon_rows": 0},
    )
    monkeypatch.setattr(train_lead_time_models, "build_model_frame", lambda **kwargs: model_frame)
    monkeypatch.setattr(train_lead_time_models, "read_standing_table", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(
        train_lead_time_models,
        "planned_constraint_tables",
        lambda *args, **kwargs: {
            "planned_gencondata": pd.DataFrame(),
            "planned_genconset": pd.DataFrame(),
            "planned_genconsetinvoke": pd.DataFrame(),
        },
    )
    monkeypatch.setattr(
        train_lead_time_models,
        "resolve_coordinates",
        lambda **kwargs: (-27.4, 151.7, {"source": "test"}),
    )

    args = train_lead_time_models.build_arg_parser().parse_args(
        [
            "--duid", "D1",
            "--days", "4",
            "--horizon-hours", "2",
            "--lead-hours", "1", "2",
            "--model-out", str(tmp_path / "leads.joblib"),
            "--metrics-out", str(tmp_path / "leads.json"),
            "--folds-out", str(tmp_path / "leads.csv"),
            "--report-out", str(tmp_path / "report.md"),
        ]
    )
    result = train_lead_time_models.run(args)

    bundle = joblib.load(tmp_path / "leads.joblib")
    assert bundle["lead_hours"] == [1, 2]
    assert sorted(bundle["lead_models"]) == [1, 2]
    # The cumulative model stays under "model" so single-horizon serving still works.
    assert hasattr(bundle["model"], "predict_proba")
    assert bundle["target_column"] == "target_constrained_next_2h"

    folds = pd.read_csv(tmp_path / "leads.csv")
    assert set(folds["lead_hours"].dropna().unique()) == {1.0, 2.0}
    assert folds["lead_hours"].isna().any(), "the cumulative target's folds are recorded too"

    report = (tmp_path / "report.md").read_text()
    assert "Per-bucket event-window backtest" in report
    assert "(t+0h, t+1h]" in report
    assert result["lead_hours"] == [1, 2]
