from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from scripts import benchmark_models
from scripts.backtest_horizon_model import ValidationWindow


def _synthetic_benchmark_frame() -> pd.DataFrame:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=480, freq="5min")
    target = [0] * len(intervals)
    target[12] = 1
    target[360] = 1
    return pd.DataFrame(
        {
            "SETTLEMENTDATE": intervals,
            "hour_sin": range(len(intervals)),
            "target_constrained_next_6h": target,
        }
    )


def test_evaluate_combination_records_skipped_invalid_fold() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=40, freq="5min")
    frame = pd.DataFrame(
        {
            "SETTLEMENTDATE": intervals,
            "feature": range(40),
            "target_constrained_next_6h": [0] * 20 + [1] + [0] * 19,
        }
    )

    row, records = benchmark_models.evaluate_combination(
        frame=frame,
        feature_cols=["feature"],
        target_column="target_constrained_next_6h",
        windows=[ValidationWindow(validation_start=intervals[20], validation_end=intervals[30])],
        model_type="logistic_regression",
        feature_set="time_only",
        days=1,
        horizon_hours=6,
    )

    assert row["evaluated_folds"] == 0
    assert row["skipped_folds"] == 1
    assert "prior_training_has_one_target_class" in row["skip_reasons"]
    assert records[0]["skipped"] is True


def test_run_benchmark_writes_schema_best_bundle_and_report(tmp_path: Path, monkeypatch) -> None:
    frame = _synthetic_benchmark_frame()

    def fake_build_backtest_frame(**kwargs):
        return frame.copy(), ["hour_sin"], "target_constrained_next_6h", {"feature_set": kwargs["feature_set"]}

    def fake_train_demo_model(**kwargs):
        model = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("classifier", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)),
            ]
        )
        model.fit(frame[["hour_sin"]], frame["target_constrained_next_6h"])
        bundle = {
            "model": model,
            "feature_columns": ["hour_sin"],
            "duid": kwargs["duid"].upper(),
            "feature_set": kwargs["feature_set"],
            "model_type": kwargs["model_type"],
            "target_column": "target_constrained_next_6h",
            "horizon_hours": kwargs["horizon_hours"],
            "metrics": {"feature_set": kwargs["feature_set"], "model_type": kwargs["model_type"]},
        }
        joblib.dump(bundle, kwargs["model_out"])
        return {"model_out": str(kwargs["model_out"]), "metrics": bundle["metrics"]}

    monkeypatch.setattr(benchmark_models, "build_backtest_frame", fake_build_backtest_frame)
    monkeypatch.setattr(benchmark_models, "train_demo_model", fake_train_demo_model)

    args = benchmark_models.build_arg_parser().parse_args(
        [
            "--duid",
            "D1",
            "--days",
            "1",
            "--horizon-hours",
            "6",
            "--feature-sets",
            "time_only",
            "--model-types",
            "logistic_regression",
            "--out",
            str(tmp_path / "benchmark.csv"),
            "--best-model-out",
            str(tmp_path / "best.joblib"),
            "--report-out",
            str(tmp_path / "report.md"),
            "--feature-importance-out",
            str(tmp_path / "importance.csv"),
            "--max-folds",
            "1",
        ]
    )

    result = benchmark_models.run_benchmark(args)
    benchmark = pd.read_csv(result["benchmark_path"])
    bundle = joblib.load(tmp_path / "best.joblib")
    importance = pd.read_csv(tmp_path / "importance.csv")

    assert list(benchmark.columns) == benchmark_models.BENCHMARK_COLUMNS
    assert benchmark.loc[0, "evaluated_folds"] == 1
    assert bundle["feature_set"] == result["best_row"]["feature_set"]
    assert bundle["model_type"] == result["best_row"]["model"]
    assert "hour_sin" in importance["feature"].tolist()
    assert (tmp_path / "report.md").exists()
