from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.aemo_constraint_demo.site_registry import REGISTRY_COLUMNS, build_site_registry, read_duid_list


def _registration() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Station Name": ["Cathedral Rocks", "Woolooga Solar Farm", "Some Battery"],
            "Classification": ["Non-Scheduled*", "Semi-Scheduled", "Scheduled"],
            "Fuel Source - Primary": ["Wind", "Solar", "Battery Storage"],
            "Technology Type - Descriptor": ["Wind - Onshore", "Photovoltaic Tracking Flat panel", "Battery"],
            "DUID": ["CATHROCK", "WOOLGSF1", "BATT1"],
            "Reg Cap generation (MW)": ["66", "222.5", "50"],
            "Max Cap generation (MW)": ["66", None, "50"],
        }
    )


def _summary() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "DUID": ["CATHROCK", "CATHROCK", "WOOLGSF1", "BATT1"],
            "START_DATE": ["2025/07/01 00:00:00", "2026/07/01 00:00:00", "2026/07/01 00:00:00", "2026/07/01 00:00:00"],
            "REGIONID": ["SA1", "SA1", "QLD1", "VIC1"],
            "STATIONID": ["OLDID", "CATHROCK", "WOOLGSF", "BATT"],
            "SCHEDULE_TYPE": ["NON-SCHEDULED", "SEMI-SCHEDULED", "SEMI-SCHEDULED", "SCHEDULED"],
            "DISPATCHTYPE": ["GENERATOR"] * 3 + ["BIDIRECTIONAL"],
            "CONNECTIONPOINTID": ["SCRK", "SCRK", "QWLS1W", "VBAT"],
        }
    )


def _detail() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "DUID": ["WOOLGSF1"],
            "EFFECTIVEDATE": ["2024/06/03 00:00:00"],
            "REGISTEREDCAPACITY": ["222"],
            "MAXCAPACITY": ["176"],
        }
    )


def test_build_site_registry_joins_latest_standing_rows() -> None:
    registry = build_site_registry(["cathrock", "WOOLGSF1"], _registration(), _summary(), _detail())

    assert list(registry.columns) == REGISTRY_COLUMNS
    cath = registry.set_index("duid").loc["CATHROCK"]
    assert cath["station_id"] == "CATHROCK"  # latest START_DATE wins
    assert cath["schedule_type"] == "SEMI-SCHEDULED"
    assert cath["classification"] == "Non-Scheduled*"
    assert cath["fuel"] == "WIND"
    assert cath["region_id"] == "SA1"


def test_build_site_registry_prefers_registration_capacity_and_falls_back_to_dudetail() -> None:
    registry = build_site_registry(["WOOLGSF1"], _registration(), _summary(), _detail()).iloc[0]

    assert registry["registered_capacity_mw"] == pytest.approx(222.5)
    assert registry["max_capacity_mw"] == pytest.approx(176.0)  # missing in the list, taken from DUDETAIL


def test_build_site_registry_fails_on_missing_duid() -> None:
    with pytest.raises(ValueError, match="NOPE1"):
        build_site_registry(["CATHROCK", "NOPE1"], _registration(), _summary(), _detail())


def test_build_site_registry_rejects_non_solar_wind_units() -> None:
    with pytest.raises(ValueError, match="solar and wind"):
        build_site_registry(["BATT1"], _registration(), _summary(), _detail())


def test_read_duid_list_skips_comments_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "duids.txt"
    path.write_text("# header\ncathrock\n\nWOOLGSF1  # solar\nCATHROCK\n")

    assert read_duid_list(path) == ["CATHROCK", "WOOLGSF1"]
