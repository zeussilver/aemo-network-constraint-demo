from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from src.aemo_constraint_demo import frames as frames_module
from src.aemo_constraint_demo.forecast import (
    FALLBACK_WEATHER_WARNING,
    forecast_from_bundle,
    future_intervals,
    planned_constraints_for_intervals,
)

START = "2026-05-01 04:05:00"
PERIODS = 288
LAST_INTERVAL = pd.Timestamp(START) + pd.Timedelta(minutes=5 * (PERIODS - 1))

TIME_FEATURES = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos", "is_weekend"]


class RampProbabilityModel:
    """Probability rises across the frame, so the origin row is identifiable."""

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        positive = np.linspace(0.0, 1.0, len(features))
        return np.column_stack([1 - positive, positive])


def _dispatch_tables() -> dict[str, pd.DataFrame]:
    timestamps = pd.date_range(START, periods=PERIODS, freq="5min")
    cleared = np.round(10 + 5 * np.sin(np.linspace(0, 6, PERIODS)), 2)
    dispatchload = pd.DataFrame(
        {
            "SETTLEMENTDATE": timestamps.astype(str),
            "DUID": ["D1"] * PERIODS,
            "TOTALCLEARED": cleared.astype(str),
            "INITIALMW": np.round(cleared + 0.3, 2).astype(str),
            "AVAILABILITY": np.round(cleared + 2.0, 2).astype(str),
        }
    )
    dispatchconstraint = pd.DataFrame(
        {
            "SETTLEMENTDATE": timestamps[100:110].astype(str),
            "CONSTRAINTID": ["C1"] * 10,
            "MARGINALVALUE": ["5.0"] * 10,
            "VIOLATIONDEGREE": ["0"] * 10,
            "INTERVENTION": ["0"] * 10,
        }
    )
    return {"DISPATCHLOAD": dispatchload, "DISPATCHCONSTRAINT": dispatchconstraint}


def _weather() -> pd.DataFrame:
    hours = pd.date_range("2026-05-01 00:00:00", periods=48, freq="h")
    return pd.DataFrame({"time": hours, "temperature_2m": np.linspace(8.0, 26.0, len(hours))})


@pytest.fixture
def offline_nemweb(monkeypatch):
    monkeypatch.setattr(frames_module, "load_next_day_dispatch_tables", lambda **kwargs: _dispatch_tables())
    monkeypatch.setattr(frames_module, "fetch_open_meteo_history", lambda *args, **kwargs: _weather())


def _write_bundle(tmp_path: Path, **overrides) -> Path:
    bundle = {
        "model": RampProbabilityModel(),
        "feature_columns": list(TIME_FEATURES),
        "duid": "D1",
        "connection_point": "CP1",
        "latitude": -27.4,
        "longitude": 151.7,
        "relevant_genconids": ["C1"],
        "planned_genconset": pd.DataFrame(),
        "planned_genconsetinvoke": pd.DataFrame(),
        "metrics": {"model_type": "logistic_regression"},
        "trained_at_utc": "2026-05-01T00:00:00+00:00",
        "horizon_hours": 6,
        "target_column": "target_constrained_next_6h",
        "feature_set": "time_only",
        "no_weather": True,
    }
    bundle.update(overrides)
    path = tmp_path / "model.joblib"
    joblib.dump(bundle, path)
    return path


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


def test_forecast_anchors_on_the_latest_observed_interval(tmp_path: Path, offline_nemweb) -> None:
    result = forecast_from_bundle(_write_bundle(tmp_path), duid="d1")

    assert result["duid"] == "D1"
    assert result["as_of"] == LAST_INTERVAL.isoformat()
    assert result["horizon_end"] == (LAST_INTERVAL + pd.Timedelta(hours=6)).isoformat()
    assert result["data_lag_hours"] > 0
    # The ramp model peaks on its last row, which must be the row we score.
    assert result["constraint_risk"]["probability"] == 1.0
    assert result["constraint_risk"]["risk_level"] == "high"
    assert LAST_INTERVAL.isoformat() in result["constraint_risk"]["statement"]
    assert result["dispatch_source"]["interval_count"] == PERIODS
    assert result["dispatch_source"]["last_interval"] == LAST_INTERVAL.isoformat()


def test_forecast_reports_full_feature_coverage_for_a_lag_model(tmp_path: Path, offline_nemweb) -> None:
    lag_features = TIME_FEATURES + ["temperature_2m", "totalcleared_lag_6h", "totalcleared_rolling_mean_3h"]
    path = _write_bundle(
        tmp_path,
        feature_columns=lag_features,
        feature_set="time_weather_dispatch_lag",
        no_weather=False,
    )

    result = forecast_from_bundle(path, duid="D1")

    assert result["feature_coverage"] == {
        "required": len(lag_features),
        "observed": len(lag_features),
        "imputed": 0,
        "imputed_examples": [],
    }
    assert result["weather_source"] == "open_meteo_history"
    assert result["weather_warning"] is None


def test_forecast_flags_imputed_features_when_weather_is_disabled(tmp_path: Path, offline_nemweb) -> None:
    path = _write_bundle(tmp_path, feature_columns=TIME_FEATURES + ["temperature_2m"], no_weather=False)

    result = forecast_from_bundle(path, duid="D1", use_weather=False)

    assert result["feature_coverage"]["imputed"] == 1
    assert result["feature_coverage"]["imputed_examples"] == ["temperature_2m"]
    assert result["weather_source"] == "not_requested"
    assert result["weather_warning"] == FALLBACK_WEATHER_WARNING


def test_forecast_falls_back_when_open_meteo_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(frames_module, "load_next_day_dispatch_tables", lambda **kwargs: _dispatch_tables())

    def boom(*args, **kwargs):
        raise RuntimeError("open-meteo down")

    monkeypatch.setattr(frames_module, "fetch_open_meteo_history", boom)
    path = _write_bundle(tmp_path, feature_columns=TIME_FEATURES + ["temperature_2m"], no_weather=False)

    result = forecast_from_bundle(path, duid="D1")

    assert result["weather_source"] == "fallback_imputed"
    assert result["weather_warning"] == FALLBACK_WEATHER_WARNING
    assert result["feature_coverage"]["imputed"] == 1


def test_forecast_summarises_recent_risk_history_ending_at_the_origin(tmp_path: Path, offline_nemweb) -> None:
    result = forecast_from_bundle(_write_bundle(tmp_path), duid="D1")
    history = result["recent_risk_history"]

    # 24 hours of trailing origins, inclusive of both ends.
    assert len(history) == 25
    assert history[-1]["origin"] == result["as_of"]
    assert history[-1]["probability"] == result["constraint_risk"]["probability"]
    origins = [pd.Timestamp(point["origin"]) for point in history]
    assert origins == sorted(origins)
    assert origins[-1] - origins[-2] == pd.Timedelta(hours=1)


def test_forecast_summarises_planned_constraints_over_the_horizon(tmp_path: Path, offline_nemweb) -> None:
    path = _write_bundle(
        tmp_path,
        planned_genconset=pd.DataFrame([{"GENCONSETID": "SET_A", "GENCONID": "C1"}]),
        planned_genconsetinvoke=pd.DataFrame(
            [
                {
                    "GENCONSETID": "SET_A",
                    "STARTDATE": "2026-05-01",
                    "STARTPERIOD": "1",
                    "ENDDATE": "2026-05-03",
                    "ENDPERIOD": "288",
                    "STARTAUTHORISEDBY": "AEMO",
                }
            ]
        ),
    )

    planned = forecast_from_bundle(path, duid="D1")["planned_constraints"]

    assert [item["genconid"] for item in planned] == ["C1"]
    assert planned[0]["active_intervals"] == 6 * 12
    # The horizon starts one interval after the origin, never on it.
    assert pd.Timestamp(planned[0]["first_interval"]) == LAST_INTERVAL + pd.Timedelta(minutes=5)


def test_forecast_infers_model_horizon_when_omitted(tmp_path: Path, offline_nemweb) -> None:
    result = forecast_from_bundle(_write_bundle(tmp_path, horizon_hours=3), duid="D1")

    assert result["horizon_hours"] == 3
    assert result["horizon_end"] == (LAST_INTERVAL + pd.Timedelta(hours=3)).isoformat()


def test_forecast_defaults_the_feature_set_for_legacy_bundles(tmp_path: Path, offline_nemweb) -> None:
    path = _write_bundle(tmp_path, feature_set=None, feature_columns=TIME_FEATURES + ["temperature_2m"], no_weather=False)

    result = forecast_from_bundle(path, duid="D1")

    assert result["feature_coverage"]["imputed"] == 0
    assert result["model"]["feature_set"] is None


def test_forecast_rejects_mismatched_horizon(tmp_path: Path, offline_nemweb) -> None:
    with pytest.raises(ValueError, match="does not match model bundle"):
        forecast_from_bundle(_write_bundle(tmp_path), duid="D1", horizon_hours=1)


def test_forecast_rejects_wrong_duid(tmp_path: Path, offline_nemweb) -> None:
    with pytest.raises(ValueError, match="trained for D1"):
        forecast_from_bundle(_write_bundle(tmp_path), duid="D2")


def test_forecast_reports_missing_dispatch_history(tmp_path: Path, monkeypatch) -> None:
    empty = {"DISPATCHLOAD": _dispatch_tables()["DISPATCHLOAD"], "DISPATCHCONSTRAINT": pd.DataFrame()}
    empty["DISPATCHLOAD"] = empty["DISPATCHLOAD"].assign(DUID="OTHER")
    monkeypatch.setattr(frames_module, "load_next_day_dispatch_tables", lambda **kwargs: empty)

    with pytest.raises(ValueError, match="No DISPATCHLOAD rows"):
        forecast_from_bundle(_write_bundle(tmp_path), duid="D1")
