from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd

from src.aemo_constraint_demo.scan import horizon_label_column
from src.aemo_constraint_demo import train as train_module


def test_train_demo_model_builds_lightweight_bundle(tmp_path: Path, monkeypatch) -> None:
    intervals = pd.date_range("2026-05-01 00:05:00", periods=15, freq="5min")

    tables = {
        "DUDETAIL": pd.DataFrame([{"DUID": "D1", "CONNECTIONPOINTID": "CP1", "EFFECTIVEDATE": "2026-01-01"}]),
        "SPDCONNECTIONPOINTCONSTRAINT": pd.DataFrame(
            [{"CONNECTIONPOINTID": "CP1", "GENCONID": "C1", "FACTOR": "1", "BIDTYPE": "ENERGY"}]
        ),
        "GENCONDATA": pd.DataFrame([{"GENCONID": "C1", "DESCRIPTION": "Constraint one"}]),
        "GENCONSET": pd.DataFrame([{"GENCONSETID": "SET_A", "GENCONID": "C1"}]),
        "GENCONSETINVOKE": pd.DataFrame(
            [
                {
                    "GENCONSETID": "SET_A",
                    "STARTDATE": "2026-05-01",
                    "STARTPERIOD": "1",
                    "ENDDATE": "2026-05-01",
                    "ENDPERIOD": "3",
                    "STARTAUTHORISEDBY": "AEMO",
                }
            ]
        ),
    }
    dispatchload = pd.DataFrame({"SETTLEMENTDATE": intervals.astype(str), "DUID": "D1", "TOTALCLEARED": range(15)})
    dispatchconstraint = pd.DataFrame(
        {
            "SETTLEMENTDATE": [str(intervals[i]) for i in [1, 4, 7, 13]],
            "CONSTRAINTID": ["C1", "C1", "C1", "C1"],
            "MARGINALVALUE": ["0.4", "0.3", "0.5", "0.2"],
            "VIOLATIONDEGREE": ["0", "0", "0", "0"],
            "INTERVENTION": ["0", "0", "0", "0"],
        }
    )

    monkeypatch.setattr(train_module, "read_monthly_table", lambda table, **kwargs: tables[table])
    monkeypatch.setattr(
        train_module,
        "load_next_day_dispatch_tables",
        lambda **kwargs: {"DISPATCHLOAD": dispatchload, "DISPATCHCONSTRAINT": dispatchconstraint},
    )

    model_out = tmp_path / "model.joblib"
    result = train_module.train_demo_model(
        duid="d1",
        latitude=-27.4,
        longitude=151.7,
        days=1,
        cache_dir=tmp_path / "cache",
        model_out=model_out,
        no_weather=True,
    )

    bundle = joblib.load(model_out)
    assert result["duid"] == "D1"
    assert result["connection_point"] == "CP1"
    assert result["relevant_genconids_count"] == 1
    assert bundle["planned_genconset"]["GENCONSETID"].tolist() == ["SET_A"]
    assert bundle["planned_genconsetinvoke"]["GENCONSETID"].tolist() == ["SET_A"]
    assert bundle["no_weather"] is True
    assert model_out.exists()


def test_train_demo_model_falls_back_to_constant_model_for_one_class_window(tmp_path: Path, monkeypatch) -> None:
    intervals = pd.date_range("2026-05-01 00:05:00", periods=15, freq="5min")
    tables = {
        "DUDETAIL": pd.DataFrame([{"DUID": "D1", "CONNECTIONPOINTID": "CP1"}]),
        "SPDCONNECTIONPOINTCONSTRAINT": pd.DataFrame(
            [{"CONNECTIONPOINTID": "CP1", "GENCONID": "C1", "FACTOR": "1", "BIDTYPE": "ENERGY"}]
        ),
        "GENCONDATA": pd.DataFrame([{"GENCONID": "C1"}]),
        "GENCONSET": pd.DataFrame([{"GENCONSETID": "SET_A", "GENCONID": "C1"}]),
        "GENCONSETINVOKE": pd.DataFrame(),
    }
    dispatchload = pd.DataFrame({"SETTLEMENTDATE": intervals.astype(str), "DUID": "D1", "TOTALCLEARED": range(15)})
    dispatchconstraint = pd.DataFrame(
        columns=["SETTLEMENTDATE", "CONSTRAINTID", "MARGINALVALUE", "VIOLATIONDEGREE", "INTERVENTION"]
    )

    monkeypatch.setattr(train_module, "read_monthly_table", lambda table, **kwargs: tables[table])
    monkeypatch.setattr(
        train_module,
        "load_next_day_dispatch_tables",
        lambda **kwargs: {"DISPATCHLOAD": dispatchload, "DISPATCHCONSTRAINT": dispatchconstraint},
    )

    model_out = tmp_path / "constant.joblib"
    result = train_module.train_demo_model(
        duid="D1",
        latitude=-27.4,
        longitude=151.7,
        days=1,
        cache_dir=tmp_path / "cache",
        model_out=model_out,
        no_weather=True,
    )

    bundle = joblib.load(model_out)
    assert result["metrics"]["model_type"] == "constant_probability_baseline"
    features = pd.DataFrame({col: [0.0] for col in bundle["feature_columns"]})
    assert bundle["model"].predict_proba(features).shape == (1, 2)


def test_train_arg_parser_accepts_horizon_hours() -> None:
    args = train_module.build_arg_parser().parse_args(
        [
            "--duid",
            "D1",
            "--lat",
            "-27.4",
            "--lon",
            "151.7",
            "--horizon-hours",
            "6",
            "--backtest-window-hours",
            "24",
            "--feature-set",
            "time_weather_constraint_lag",
            "--model-type",
            "random_forest",
        ]
    )

    assert args.horizon_hours == 6
    assert args.backtest_window_hours == 24
    assert args.feature_set == "time_weather_constraint_lag"
    assert args.model_type == "random_forest"


def test_model_type_selection_returns_requested_two_class_models() -> None:
    frame = pd.DataFrame(
        {
            "feature": list(range(20)),
            "target": [0, 1] * 10,
        }
    )

    for model_type in ["logistic_regression", "random_forest", "hist_gradient_boosting"]:
        model, resolved_model_type, y_train = train_module._fit_probability_model(
            frame,
            feature_cols=["feature"],
            target_column="target",
            model_type=model_type,
        )

        assert resolved_model_type == model_type
        assert y_train.nunique() == 2
        assert model.predict_proba(frame[["feature"]]).shape == (20, 2)


def test_supplemental_positive_backtest_uses_prior_two_class_training_window() -> None:
    intervals = pd.date_range("2026-05-01 00:00:00", periods=36, freq="5min")
    frame = pd.DataFrame(
        {
            "SETTLEMENTDATE": intervals,
            "feature": range(36),
            "target": [1 if idx in {5, 25} else 0 for idx in range(36)],
        }
    )

    result = train_module.supplemental_positive_backtest(
        frame=frame,
        feature_cols=["feature"],
        target_column="target",
        window_hours=1,
    )

    assert result["available"] is True
    assert result["train_positive_samples"] == 1
    assert result["validation_positive_samples"] == 1
    assert result["validation_samples"] == 12
    assert result["average_precision"] is not None
    assert result["roc_auc"] is not None


def test_train_demo_model_uses_horizon_target_and_writes_metrics(tmp_path: Path, monkeypatch) -> None:
    intervals = pd.date_range("2026-05-01 00:05:00", periods=40, freq="5min")
    tables = {
        "DUDETAIL": pd.DataFrame([{"DUID": "D1", "CONNECTIONPOINTID": "CP1", "EFFECTIVEDATE": "2026-01-01"}]),
        "SPDCONNECTIONPOINTCONSTRAINT": pd.DataFrame(
            [{"CONNECTIONPOINTID": "CP1", "GENCONID": "C1", "FACTOR": "1", "BIDTYPE": "ENERGY"}]
        ),
        "GENCONDATA": pd.DataFrame([{"GENCONID": "C1", "DESCRIPTION": "Constraint one"}]),
        "GENCONSET": pd.DataFrame([{"GENCONSETID": "SET_A", "GENCONID": "C1"}]),
        "GENCONSETINVOKE": pd.DataFrame(),
    }
    dispatchload = pd.DataFrame({"SETTLEMENTDATE": intervals.astype(str), "DUID": "D1", "TOTALCLEARED": range(40)})
    dispatchconstraint = pd.DataFrame(
        {
            "SETTLEMENTDATE": [str(intervals[i]) for i in [5, 25]],
            "CONSTRAINTID": ["C1", "C1"],
            "MARGINALVALUE": ["0.4", "0.3"],
            "VIOLATIONDEGREE": ["0", "0"],
            "INTERVENTION": ["0", "0"],
        }
    )

    monkeypatch.setattr(train_module, "read_monthly_table", lambda table, **kwargs: tables[table])
    monkeypatch.setattr(
        train_module,
        "load_next_day_dispatch_tables",
        lambda **kwargs: {"DISPATCHLOAD": dispatchload, "DISPATCHCONSTRAINT": dispatchconstraint},
    )

    model_out = tmp_path / "horizon.joblib"
    metrics_out = tmp_path / "horizon_metrics.json"
    result = train_module.train_demo_model(
        duid="D1",
        latitude=-27.4,
        longitude=151.7,
        days=1,
        cache_dir=tmp_path / "cache",
        model_out=model_out,
        metrics_out=metrics_out,
        horizon_hours=1,
        no_weather=True,
    )

    bundle = joblib.load(model_out)
    metrics = json.loads(metrics_out.read_text(encoding="utf-8"))
    target_column = horizon_label_column(1)
    assert result["target_column"] == target_column
    assert bundle["target_column"] == target_column
    assert metrics["horizon_hours"] == 1
    assert metrics["target_column"] == target_column
    assert metrics["total_samples"] == 28
    assert "supplemental_positive_backtest" in metrics
    assert all(not col.startswith("target_constrained") for col in metrics["features"])
    assert metrics_out.exists()


def test_train_demo_model_bundle_records_feature_set_and_model_type(tmp_path: Path, monkeypatch) -> None:
    intervals = pd.date_range("2026-05-01 00:05:00", periods=80, freq="5min")
    tables = {
        "DUDETAIL": pd.DataFrame([{"DUID": "D1", "CONNECTIONPOINTID": "CP1", "EFFECTIVEDATE": "2026-01-01"}]),
        "SPDCONNECTIONPOINTCONSTRAINT": pd.DataFrame(
            [{"CONNECTIONPOINTID": "CP1", "GENCONID": "C1", "FACTOR": "1", "BIDTYPE": "ENERGY"}]
        ),
        "GENCONDATA": pd.DataFrame([{"GENCONID": "C1", "DESCRIPTION": "Constraint one"}]),
        "GENCONSET": pd.DataFrame([{"GENCONSETID": "SET_A", "GENCONID": "C1"}]),
        "GENCONSETINVOKE": pd.DataFrame(),
    }
    dispatchload = pd.DataFrame({"SETTLEMENTDATE": intervals.astype(str), "DUID": "D1", "TOTALCLEARED": range(80)})
    dispatchconstraint = pd.DataFrame(
        {
            "SETTLEMENTDATE": [str(intervals[i]) for i in [1, 20, 40, 60]],
            "CONSTRAINTID": ["C1", "C1", "C1", "C1"],
            "MARGINALVALUE": ["0.4", "0.3", "0.5", "0.2"],
            "VIOLATIONDEGREE": ["0", "0", "0", "0"],
            "INTERVENTION": ["0", "0", "0", "0"],
        }
    )

    monkeypatch.setattr(train_module, "read_monthly_table", lambda table, **kwargs: tables[table])
    monkeypatch.setattr(
        train_module,
        "load_next_day_dispatch_tables",
        lambda **kwargs: {"DISPATCHLOAD": dispatchload, "DISPATCHCONSTRAINT": dispatchconstraint},
    )

    model_out = tmp_path / "rf.joblib"
    result = train_module.train_demo_model(
        duid="D1",
        latitude=-27.4,
        longitude=151.7,
        days=1,
        cache_dir=tmp_path / "cache",
        model_out=model_out,
        horizon_hours=0,
        feature_set="time_weather_dispatch_constraint_lag",
        model_type="random_forest",
        no_weather=True,
    )

    bundle = joblib.load(model_out)
    assert result["metrics"]["feature_set"] == "time_weather_dispatch_constraint_lag"
    assert result["metrics"]["model_type"] == "random_forest"
    assert bundle["feature_set"] == "time_weather_dispatch_constraint_lag"
    assert bundle["model_type"] == "random_forest"
    assert "totalcleared_lag_1h" in bundle["feature_columns"]
    assert "target_constrained_lag_1h" in bundle["feature_columns"]
