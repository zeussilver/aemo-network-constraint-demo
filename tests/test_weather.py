from __future__ import annotations

import pandas as pd

from src.aemo_constraint_demo import weather


class DummyResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_fetch_open_meteo_history_sends_expected_params(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    payload = {"hourly": {"time": ["2026-05-01T00:00"], "temperature_2m": ["20.5"]}}

    def fake_get(url: str, params: dict, timeout: int) -> DummyResponse:
        calls.append((url, params))
        return DummyResponse(payload)

    monkeypatch.setattr(weather.requests, "get", fake_get)

    df = weather.fetch_open_meteo_history(-27.4, 151.7, "2026-05-01", "2026-05-02", variables=["temperature_2m"])

    assert calls[0][0] == "https://archive-api.open-meteo.com/v1/archive"
    assert calls[0][1]["hourly"] == "temperature_2m"
    assert df.loc[0, "temperature_2m"] == 20.5


def test_merge_weather_to_intervals_uses_nearest_previous_hour_with_tolerance() -> None:
    intervals = pd.DataFrame(
        {
            "SETTLEMENTDATE": pd.to_datetime(
                ["2026-05-01 10:30:00", "2026-05-01 11:45:00", "2026-05-01 12:45:00"]
            )
        }
    )
    hourly = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-05-01 10:00:00", "2026-05-01 11:00:00"]),
            "temperature_2m": [20, 21],
        }
    )

    merged = weather.merge_weather_to_intervals(intervals, hourly)

    assert merged["temperature_2m"].tolist()[:2] == [20.0, 21.0]
    assert pd.isna(merged.loc[2, "temperature_2m"])


def test_merge_weather_to_intervals_adds_empty_weather_columns() -> None:
    intervals = pd.DataFrame({"SETTLEMENTDATE": ["2026-05-01 10:30:00"]})

    merged = weather.merge_weather_to_intervals(intervals, pd.DataFrame())

    for col in weather.HOURLY_VARIABLES:
        assert col in merged.columns
