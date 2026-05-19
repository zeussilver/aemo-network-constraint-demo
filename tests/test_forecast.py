from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from src.aemo_constraint_demo.forecast import (
    forecast_from_bundle,
    future_intervals,
    planned_constraints_for_intervals,
)


class ConstantProbabilityModel:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        positive = np.full(len(features), self.probability)
        return np.column_stack([1 - positive, positive])


def test_future_intervals_builds_5_minute_grid() -> None:
    intervals = future_intervals(horizon_hours=1, start="2026-05-01 08:00:00")

    assert len(intervals) == 12
    assert intervals["SETTLEMENTDATE"].iloc[0] == pd.Timestamp("2026-05-01 08:00:00")
    assert intervals["SETTLEMENTDATE"].iloc[-1] == pd.Timestamp("2026-05-01 08:55:00")


def test_planned_constraints_for_intervals_maps_invoked_sets_to_relevant_genconids() -> None:
    bundle = {
        "relevant_genconids": ["C1", "C2"],
        "planned_genconset": pd.DataFrame(
            [
                {"GENCONSETID": "SET_A", "GENCONID": "C1"},
                {"GENCONSETID": "SET_A", "GENCONID": "C2"},
                {"GENCONSETID": "SET_B", "GENCONID": "OTHER"},
            ]
        ),
        "planned_genconsetinvoke": pd.DataFrame(
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
    intervals = pd.DataFrame(
        {"SETTLEMENTDATE": pd.to_datetime(["2026-05-01 04:05:00", "2026-05-01 04:15:00", "2026-05-01 04:20:00"])}
    )

    planned = planned_constraints_for_intervals(bundle, intervals)

    assert planned[pd.Timestamp("2026-05-01 04:05:00")] == ["C1", "C2"]
    assert planned[pd.Timestamp("2026-05-01 04:15:00")] == ["C1", "C2"]
    assert planned[pd.Timestamp("2026-05-01 04:20:00")] == []


def test_forecast_from_bundle_returns_probability_rows(tmp_path: Path) -> None:
    model_path = tmp_path / "model.joblib"
    joblib.dump(
        {
            "model": ConstantProbabilityModel(0.73),
            "feature_columns": ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos", "is_weekend"],
            "duid": "D1",
            "connection_point": "CP1",
            "latitude": -27.4,
            "longitude": 151.7,
            "relevant_genconids": ["C1"],
            "planned_genconset": pd.DataFrame(),
            "planned_genconsetinvoke": pd.DataFrame(),
            "metrics": {"rows": 10},
            "trained_at_utc": "2026-05-01T00:00:00+00:00",
            "horizon_hours": 1,
            "target_column": "target_constrained_next_1h",
            "no_weather": True,
        },
        model_path,
    )

    result = forecast_from_bundle(model_path, duid="d1", horizon_hours=1, start="2026-05-01 08:00:00")

    assert result["duid"] == "D1"
    assert result["model"]["target_column"] == "target_constrained_next_1h"
    assert len(result["forecast"]) == 12
    assert result["forecast"][0]["probability"] == 0.73
    assert result["forecast"][0]["ml_constraint_probability"] == 0.73
    assert result["forecast"][0]["ml_predicted_constrained"] is True
    assert result["forecast"][0]["risk_level"] == "high"


def test_forecast_from_bundle_rejects_wrong_duid(tmp_path: Path) -> None:
    model_path = tmp_path / "model.joblib"
    joblib.dump(
        {
            "model": ConstantProbabilityModel(0.2),
            "feature_columns": ["hour_sin"],
            "duid": "D1",
            "latitude": 0,
            "longitude": 0,
            "relevant_genconids": [],
            "no_weather": True,
        },
        model_path,
    )

    with pytest.raises(ValueError, match="trained for D1"):
        forecast_from_bundle(model_path, duid="D2", horizon_hours=1, start="2026-05-01 08:00:00")
