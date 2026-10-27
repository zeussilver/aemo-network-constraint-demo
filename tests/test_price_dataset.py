from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd
import pytest

from src.aemo_constraint_demo import price_dataset

NEM = "+10:00"


def stamp(text: str) -> str:
    return f"{text}:00{NEM}"


def write_gz(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as handle:
        frame.to_csv(handle, index=False)
    return path


def five_minute_frame() -> pd.DataFrame:
    """Two hours of five-minute intervals, 00:05 to 02:00."""
    stamps = pd.date_range("2026-08-01 00:05", "2026-08-01 02:00", freq="5min", tz=NEM)
    return pd.DataFrame(
        {
            "settlementdate": [s.isoformat() for s in stamps],
            "regionid": "NSW1",
            "rrp": [50.0 + index for index in range(len(stamps))],
        }
    )


def demand_frame() -> pd.DataFrame:
    stamps = pd.date_range("2026-08-01 00:05", "2026-08-01 02:00", freq="5min", tz=NEM)
    return pd.DataFrame(
        {
            "settlementdate": [s.isoformat() for s in stamps],
            "regionid": "NSW1",
            "totaldemand": 8000.0,
            "availablegeneration": 12000.0,
            "ss_solar_clearedmw": 0.0,
            "ss_wind_clearedmw": 800.0,
        }
    )


def forecast_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = [
        # published 01:00, for the half hours ending 01:30 and 02:00
        ("2026080102", stamp("2026-08-01 01:00"), stamp("2026-08-01 01:30"), 1, 30),
        ("2026080102", stamp("2026-08-01 01:00"), stamp("2026-08-01 02:00"), 2, 60),
        # published after the interval it describes: never usable
        ("2026080103", stamp("2026-08-01 02:30"), stamp("2026-08-01 02:00"), 1, -30),
    ]
    summary = pd.DataFrame(
        {
            "published": [row[1] for row in rows],
            "predispatchseqno": [row[0] for row in rows],
            "datetime": [row[2] for row in rows],
            "regionid": "NSW1",
            "periodid": [row[3] for row in rows],
            "horizon_min": [row[4] for row in rows],
            "n_scenarios": 43,
            "min": 40.0, "max": 200.0, "mean": 80.0, "std": 20.0,
            "p05": 45.0, "p10": 50.0, "p25": 60.0, "p50": 75.0,
            "p75": 95.0, "p90": 120.0, "p95": 150.0,
            "iqr": 35.0, "range": 160.0,
            "share_negative": 0.0, "share_above_300": 0.0,
            "scenario_1": 70.0,
        }
    )
    scenarios = pd.DataFrame(
        {
            "published": [row[1] for row in rows],
            "predispatchseqno": [row[0] for row in rows],
            "datetime": [row[2] for row in rows],
            "regionid": "NSW1",
            "periodid": [row[3] for row in rows],
            "rrpeep1": 70.0,
            "rrpeep37": [77.0, 88.0, 99.0],
        }
    )
    return summary, scenarios


def price_dir(tmp_path: Path, with_raw: bool = True) -> Path:
    out = tmp_path / "price"
    write_gz(out / "actual_price_2026-08.csv.gz", five_minute_frame())
    write_gz(out / "region_demand_2026-08.csv.gz", demand_frame())
    summary, scenarios = forecast_frames()
    write_gz(out / "sensitivity_summary_20260801_20260807.csv.gz", summary)
    if with_raw:
        write_gz(out / "sensitivity_scenarios_20260801_20260807.csv.gz", scenarios)
    return out


def test_trading_end_names_a_half_hour_by_when_it_ends() -> None:
    stamps = pd.Series(pd.to_datetime(["2026-08-01 00:05", "2026-08-01 00:30", "2026-08-01 00:35"]))

    ends = price_dataset.trading_end(stamps)

    assert list(ends.dt.strftime("%H:%M")) == ["00:30", "00:30", "01:00"]


def test_trading_prices_average_the_six_five_minute_prices(tmp_path: Path) -> None:
    frame = price_dataset.load_five_minute(price_dir(tmp_path), "NSW1")

    trading = price_dataset.trading_prices(frame).set_index("target")

    first = trading.iloc[0]
    assert first["intervals"] == 6
    assert first["actual_rrp"] == pytest.approx(52.5)  # 50..55
    assert first["actual_rrp_max"] == 55.0


def test_build_dataset_joins_only_what_was_known_at_publication(tmp_path: Path) -> None:
    dataset = price_dataset.build_dataset(price_dir(tmp_path), "NSW1")

    assert len(dataset) == 2  # the late forecast is dropped
    assert set(dataset["horizon_min"]) == {30, 60}

    row = dataset.set_index("horizon_min").loc[30]
    # 01:00 is the last settled five-minute interval at publication time: the 12th, 50+11
    assert row["last_rrp"] == 61.0
    assert row["aemo_base"] == 77.0
    assert row["actual_rrp"] == pytest.approx(64.5)  # the six prices stamped 01:05 to 01:30
    assert row["base_error"] == pytest.approx(77.0 - 64.5)
    assert row["last_vre_share"] == pytest.approx(0.1)


def test_build_dataset_keeps_lag_features_strictly_in_the_past(tmp_path: Path) -> None:
    dataset = price_dataset.build_dataset(price_dir(tmp_path), "NSW1")

    for _, row in dataset.iterrows():
        assert row["settlementdate"] <= row["published"]
        assert row["published"] < row["target"]


def test_max_horizon_filter(tmp_path: Path) -> None:
    dataset = price_dataset.build_dataset(price_dir(tmp_path), "NSW1", max_horizon_min=30)

    assert list(dataset["horizon_min"]) == [30]


def test_load_forecasts_says_what_to_do_when_the_base_column_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="--raw-regions NSW1"):
        price_dataset.load_forecasts(price_dir(tmp_path, with_raw=False), "NSW1")


def test_feature_columns_exclude_anything_from_after_publication(tmp_path: Path) -> None:
    dataset = price_dataset.build_dataset(price_dir(tmp_path), "NSW1")

    features = price_dataset.feature_columns(dataset)

    assert "aemo_base" in features and "p90" in features and "last_rrp" in features
    for leaking in ("actual_rrp", "actual_demand", "actual_vre_share", "base_error", "target"):
        assert leaking not in features
