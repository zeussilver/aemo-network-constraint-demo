from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app import app


class ConstantProbabilityModel:
    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        positive = np.full(len(features), 0.61)
        return np.column_stack([1 - positive, positive])


def _write_model(path: Path) -> Path:
    joblib.dump(
        {
            "model": ConstantProbabilityModel(),
            "feature_columns": ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos", "is_weekend"],
            "duid": "D1",
            "connection_point": "CP1",
            "latitude": 0,
            "longitude": 0,
            "relevant_genconids": ["C1"],
            "planned_genconset": pd.DataFrame(),
            "planned_genconsetinvoke": pd.DataFrame(),
            "no_weather": True,
            "horizon_hours": 6,
            "target_column": "target_constrained_next_6h",
            "metrics": {
                "model_type": "constant_probability_baseline",
                "horizon_hours": 6,
                "target_column": "target_constrained_next_6h",
                "positive_rate": 0.1,
            },
        },
        path,
    )
    return path


def test_health_endpoint() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_forecast_endpoint_returns_forecast_rows(tmp_path: Path) -> None:
    client = TestClient(app)
    model_path = _write_model(tmp_path / "model.joblib")

    response = client.get(
        "/forecast/D1",
        params={"horizon_hours": 6, "model_path": str(model_path), "start": "2026-05-01 08:00:00"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["duid"] == "D1"
    assert payload["connection_point"] == "CP1"
    assert payload["horizon_hours"] == 6
    assert payload["model"]["horizon_hours"] == 6
    assert payload["model"]["target_column"] == "target_constrained_next_6h"
    assert len(payload["forecast"]) == 72
    assert payload["forecast"][0]["probability"] == 0.61
    assert payload["forecast"][0]["ml_constraint_probability"] == 0.61
    assert payload["forecast"][0]["risk_level"] == "high"


def test_forecast_endpoint_infers_model_horizon_when_omitted(tmp_path: Path) -> None:
    client = TestClient(app)
    model_path = _write_model(tmp_path / "model.joblib")

    response = client.get(
        "/forecast/D1",
        params={"model_path": str(model_path), "start": "2026-05-01 08:00:00"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["horizon_hours"] == 6
    assert len(payload["forecast"]) == 72


def test_forecast_endpoint_rejects_mismatched_horizon(tmp_path: Path) -> None:
    client = TestClient(app)
    model_path = _write_model(tmp_path / "model.joblib")

    response = client.get(
        "/forecast/D1",
        params={"horizon_hours": 1, "model_path": str(model_path), "start": "2026-05-01 08:00:00"},
    )

    assert response.status_code == 400
    assert "does not match model bundle horizon_hours=6" in response.json()["detail"]


def test_forecast_endpoint_reports_missing_model(tmp_path: Path) -> None:
    client = TestClient(app)

    response = client.get("/forecast/D1", params={"model_path": str(tmp_path / "missing.joblib")})

    assert response.status_code == 404
    assert "Model file not found" in response.json()["detail"]


def test_forecast_endpoint_reports_duid_mismatch(tmp_path: Path) -> None:
    client = TestClient(app)
    model_path = _write_model(tmp_path / "model.joblib")

    response = client.get("/forecast/D2", params={"model_path": str(model_path), "start": "2026-05-01 08:00:00"})

    assert response.status_code == 400
    assert "trained for D1" in response.json()["detail"]
