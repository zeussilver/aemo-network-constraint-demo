"""Open-Meteo weather helpers for the AEMO constraint demo."""
from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd
import requests

HOURLY_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_10m",
    "shortwave_radiation",
]


def _normalise_weather_frame(payload: dict) -> pd.DataFrame:
    hourly = payload.get("hourly", {})
    if not hourly or "time" not in hourly:
        return pd.DataFrame(columns=["time", *HOURLY_VARIABLES])
    df = pd.DataFrame(hourly)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    for col in df.columns:
        if col != "time":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)


def fetch_open_meteo_history(
    latitude: float,
    longitude: float,
    start_date: str | date,
    end_date: str | date,
    timezone: str = "Australia/Melbourne",
    variables: Iterable[str] = HOURLY_VARIABLES,
) -> pd.DataFrame:
    """Fetch hourly historical/reanalysis weather from Open-Meteo."""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "hourly": ",".join(variables),
        "timezone": timezone,
    }
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    return _normalise_weather_frame(response.json())


def fetch_open_meteo_forecast(
    latitude: float,
    longitude: float,
    forecast_days: int = 3,
    timezone: str = "Australia/Melbourne",
    variables: Iterable[str] = HOURLY_VARIABLES,
) -> pd.DataFrame:
    """Fetch hourly forecast weather from Open-Meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "forecast_days": forecast_days,
        "hourly": ",".join(variables),
        "timezone": timezone,
    }
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    return _normalise_weather_frame(response.json())


def merge_weather_to_intervals(
    intervals: pd.DataFrame,
    weather: pd.DataFrame,
    time_col: str = "SETTLEMENTDATE",
) -> pd.DataFrame:
    """Merge hourly weather onto 5-minute dispatch intervals by nearest previous hour."""
    out = intervals.copy()
    if weather.empty:
        for col in HOURLY_VARIABLES:
            if col not in out.columns:
                out[col] = pd.NA
        return out

    left = out.copy()
    left["_time"] = pd.to_datetime(left[time_col], errors="coerce")
    right = weather.copy()
    right["_time"] = pd.to_datetime(right["time"], errors="coerce")
    right = right.drop(columns=["time"], errors="ignore")
    left = left.sort_values("_time")
    right = right.sort_values("_time")
    merged = pd.merge_asof(left, right, on="_time", direction="backward", tolerance=pd.Timedelta("90min"))
    return merged.drop(columns=["_time"])
