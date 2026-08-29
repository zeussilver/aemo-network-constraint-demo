from __future__ import annotations

import asyncio
from pathlib import Path

import joblib
import pandas as pd
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.aemo_constraint_demo import mcp_server


def _dispatch_window(duid: str = "CATHROCK") -> dict:
    timestamps = pd.date_range("2026-04-22 00:00", periods=4, freq="5min")
    dispatchload = pd.DataFrame(
        {
            "SETTLEMENTDATE": timestamps.astype(str),
            "DUID": [duid] * 4,
            "INITIALMW": ["10", "12", "14", "16"],
            "TOTALCLEARED": ["11", "13", "15", "17"],
            "AVAILABILITY": ["20", "20", "20", "20"],
            "SEMIDISPATCHCAP": ["0", "0", "1", "1"],
        }
    )
    dispatchconstraint = pd.DataFrame(
        {
            "SETTLEMENTDATE": [str(timestamps[2]), str(timestamps[3]), str(timestamps[3])],
            "CONSTRAINTID": ["#GC_A", "#GC_A", "#GC_OTHER"],
            "MARGINALVALUE": ["5", "0", "9"],
            "VIOLATIONDEGREE": ["0", "2", "0"],
            "INTERVENTION": ["0", "0", "0"],
        }
    )
    return {
        "dispatchload": dispatchload,
        "dispatchconstraint": dispatchconstraint,
        "trading_dates": ["2026-04-22"],
        "files_used": 1,
    }


def test_server_registers_the_documented_tools() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert [tool.name for tool in tools] == [
        "aemo_list_duid_targets",
        "aemo_get_duid_dispatch",
        "aemo_get_duid_constraints",
        "aemo_forecast_duid_constraints",
    ]
    assert all(tool.annotations.read_only_hint for tool in tools)


def test_evenly_spaced_respects_limit_and_keeps_endpoints() -> None:
    items = list(range(100))
    assert mcp_server._evenly_spaced(items, 200) == items
    sampled = mcp_server._evenly_spaced(items, 5)
    assert len(sampled) == 5
    assert sampled[0] == 0
    assert sampled[-1] == 99
    assert mcp_server._evenly_spaced(items, 1) == [99]


def test_get_duid_dispatch_summarises_window(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "_load_dispatch_window", lambda **kwargs: _dispatch_window())

    result = mcp_server.aemo_get_duid_dispatch("cathrock", days=1, source="cache", max_intervals=2)

    assert result["duid"] == "CATHROCK"
    assert result["interval_count"] == 4
    assert result["returned_intervals"] == 2
    assert result["trading_dates"] == ["2026-04-22"]
    assert result["summary"]["totalcleared_mw"] == {"min": 11.0, "mean": 14.0, "max": 17.0, "last": 17.0}
    assert result["intervals"][0]["semidispatchcap"] == 0


def test_get_duid_dispatch_reports_missing_duid(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "_load_dispatch_window", lambda **kwargs: _dispatch_window())

    with pytest.raises(ToolError, match="aemo_list_duid_targets"):
        mcp_server.aemo_get_duid_dispatch("NOPE", days=1, source="cache")


def test_get_duid_constraints_ranks_binding_constraints(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_resolve_duid",
        lambda duid, source, cache_dir: {
            "metadata": {"duid": "CATHROCK", "connection_point_id": "SCRK"},
            "relevant_genconids": ["#GC_A", "#GC_B"],
        },
    )
    monkeypatch.setattr(mcp_server, "_load_dispatch_window", lambda **kwargs: _dispatch_window())

    result = mcp_server.aemo_get_duid_constraints("CATHROCK", days=1, source="cache")

    assert result["relevant_genconid_count"] == 2
    history = result["binding_history"]
    assert history["intervals_total"] == 4
    assert history["intervals_constrained"] == 2
    # #GC_OTHER binds in the window but is not relevant to this DUID, so it is excluded.
    assert [item["genconid"] for item in history["top_binding_constraints"]] == ["#GC_A"]
    assert history["top_binding_constraints"][0]["binding_intervals"] == 2


def test_get_duid_constraints_can_skip_dispatch_download(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_resolve_duid",
        lambda duid, source, cache_dir: {
            "metadata": {"duid": "CATHROCK", "connection_point_id": "SCRK"},
            "relevant_genconids": ["#GC_A"],
        },
    )

    def fail(**kwargs):  # pragma: no cover - the tool must not reach this
        raise AssertionError("dispatch files should not be loaded")

    monkeypatch.setattr(mcp_server, "_load_dispatch_window", fail)

    result = mcp_server.aemo_get_duid_constraints("CATHROCK", include_binding_history=False)

    assert result["binding_history"] is None


def test_forecast_tool_passes_through_the_origin_anchored_payload(tmp_path: Path, monkeypatch) -> None:
    model_path = tmp_path / "demo.joblib"
    joblib.dump({"feature_columns": ["hour_sin", "temperature_2m", "totalcleared_lag_1h"]}, model_path)

    payload = {
        "duid": "CATHROCK",
        "connection_point": "SCRK",
        "horizon_hours": 6,
        "as_of": "2026-04-23T04:00:00",
        "horizon_end": "2026-04-23T10:00:00",
        "data_lag_hours": 27.5,
        "generated_at": "2026-04-24T07:30:00",
        "constraint_risk": {"probability": 0.62, "risk_level": "high", "statement": "P(...)"},
        "feature_coverage": {"required": 3, "observed": 3, "imputed": 0, "imputed_examples": []},
        "dispatch_source": {"source": "cache", "history_days": 2, "interval_count": 576},
        "model": {"model_type": "logistic_regression"},
        "weather_source": "open_meteo_history",
        "weather_warning": None,
        "relevant_genconids_count": 103,
        "planned_constraints": [{"genconid": "#GC_A", "active_intervals": 12}],
        "planned_constraints_source": "proxy",
        "recent_risk_history": [{"origin": "2026-04-23T03:00:00", "probability": 0.4, "risk_level": "medium"}],
        "limitations": "demo",
    }
    captured: dict = {}

    def fake_forecast(path, **kwargs):
        captured.update(kwargs)
        return payload

    monkeypatch.setattr(mcp_server, "forecast_from_bundle", fake_forecast)

    result = mcp_server.aemo_forecast_duid_constraints(
        "CATHROCK", model_path=str(model_path), source="cache", end_date="2026-04-22", use_weather=False
    )

    assert result["constraint_risk"]["probability"] == 0.62
    assert result["as_of"] == "2026-04-23T04:00:00"
    assert result["data_lag_hours"] == 27.5
    assert result["feature_coverage"]["imputed"] == 0
    assert result["planned_constraints"] == [{"genconid": "#GC_A", "active_intervals": 12}]
    assert result["recent_risk_history"][0]["risk_level"] == "medium"
    # Serving options must reach the forecast helper rather than being silently dropped.
    assert captured["source"] == "cache"
    assert captured["end_date"] == "2026-04-22"
    assert captured["use_weather"] is False
    assert captured["history_days"] == 2


def test_forecast_tool_lists_alternatives_for_a_missing_bundle() -> None:
    with pytest.raises(ToolError, match="Model bundle not found"):
        mcp_server.aemo_forecast_duid_constraints("CATHROCK", model_path="artifacts/does_not_exist.joblib")


def test_forecast_tool_surfaces_duid_mismatch(tmp_path: Path, monkeypatch) -> None:
    model_path = tmp_path / "demo.joblib"
    joblib.dump({"feature_columns": ["hour_sin"]}, model_path)

    def mismatch(*args, **kwargs):
        raise ValueError("This demo model was trained for CATHROCK, not OTHER.")

    monkeypatch.setattr(mcp_server, "forecast_from_bundle", mismatch)

    with pytest.raises(ToolError, match="trained for CATHROCK"):
        mcp_server.aemo_forecast_duid_constraints("OTHER", model_path=str(model_path))


def test_list_duid_targets_filters_and_ranks(tmp_path: Path, monkeypatch) -> None:
    scan = tmp_path / "scan.csv"
    pd.DataFrame(
        {
            "duid": ["AAA", "BBB", "CCC"],
            "days": [60, 60, 60],
            "horizon_hours": [6, 6, 1],
            "status": ["ok", "missing_duid", "ok"],
            "connection_point_id": ["CP1", "CP2", "CP3"],
            "relevant_genconid_count": [10, 0, 5],
            "total_intervals": [100, 0, 100],
            "positive_intervals": [5, 0, 50],
            "positive_rate": [0.05, 0.0, 0.5],
            "recommended": [True, False, False],
            "recommendation_reason": ["good", "", "too dense"],
        }
    ).to_csv(scan, index=False)

    monkeypatch.setattr(mcp_server, "_scan_artifact_path", lambda: scan)
    monkeypatch.setattr(mcp_server, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mcp_server, "_trained_models", list)

    result = mcp_server.aemo_list_duid_targets(horizon_hours=6, limit=5)

    assert result["total_rows"] == 3
    # BBB is dropped for a non-ok scan status, CCC for the horizon filter.
    assert [target["duid"] for target in result["targets"]] == ["AAA"]
    assert result["targets"][0]["positive_intervals"] == 5
    assert result["targets"][0]["recommended"] is True
