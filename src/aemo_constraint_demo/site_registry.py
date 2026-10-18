"""Build the console's site registry from AEMO standing data.

The ops console seeds its sites and units from real AEMO records, never from typed-in
values. Three public sources are joined per DUID:

- the AEMO NEM Registration and Exemption List (sheet "PU and Scheduled Loads"):
  station name, fuel, technology, classification, registered/max capacity;
- MMSDM ``DUDETAILSUMMARY``: region, station ID, schedule type, connection point;
- MMSDM ``DUDETAIL``: capacity fallback when the registration list has no value.

A DUID missing from either the registration list or ``DUDETAILSUMMARY`` is an error,
not a silently dropped row: the console must not show a site it cannot source.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .aemo_client import read_single_table_zip

REGISTRATION_SHEET = "PU and Scheduled Loads"

REGISTRY_COLUMNS = [
    "duid",
    "station_name",
    "station_id",
    "region_id",
    "fuel",
    "technology",
    "classification",
    "schedule_type",
    "dispatch_type",
    "connection_point_id",
    "registered_capacity_mw",
    "max_capacity_mw",
    "registration_list_file",
    "standing_data_file",
]

_FUELS = {"solar": "SOLAR", "wind": "WIND"}


def read_duid_list(path: str | Path) -> list[str]:
    """Read one DUID per line, ignoring blanks and ``#`` comments."""
    duids: list[str] = []
    for line in Path(path).read_text().splitlines():
        value = line.split("#", 1)[0].strip().upper()
        if value and value not in duids:
            duids.append(value)
    return duids


def read_registration_list(path: str | Path) -> pd.DataFrame:
    """Read the production-unit sheet of the AEMO Registration and Exemption List."""
    frame = pd.read_excel(path, sheet_name=REGISTRATION_SHEET, dtype=str)
    frame.columns = [str(col).strip() for col in frame.columns]
    frame["DUID"] = frame["DUID"].astype(str).str.strip().str.upper()
    return frame


def _current_rows(frame: pd.DataFrame, date_column: str) -> pd.DataFrame:
    """Keep the latest effective row per DUID."""
    frame = frame.copy()
    frame["DUID"] = frame["DUID"].astype(str).str.strip().str.upper()
    frame["_effective"] = pd.to_datetime(frame[date_column], format="%Y/%m/%d %H:%M:%S", errors="coerce")
    return frame.sort_values("_effective").groupby("DUID").tail(1).set_index("DUID")


def _number(value: object) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(number) else float(number)


def _fuel(value: object) -> str:
    fuel = _FUELS.get(str(value).strip().lower())
    if fuel is None:
        raise ValueError(f"Unsupported fuel source {value!r}; the console models solar and wind sites only.")
    return fuel


def build_site_registry(
    duids: Iterable[str],
    registration: pd.DataFrame,
    dudetailsummary: pd.DataFrame,
    dudetail: pd.DataFrame,
    registration_list_file: str = "",
    standing_data_file: str = "",
) -> pd.DataFrame:
    """Join the three standing-data sources into one row per requested DUID."""
    wanted = [str(duid).strip().upper() for duid in duids]
    reg = registration.drop_duplicates("DUID", keep="last").set_index("DUID")
    summary = _current_rows(dudetailsummary, "START_DATE")
    detail = _current_rows(dudetail, "EFFECTIVEDATE")

    missing = [duid for duid in wanted if duid not in reg.index or duid not in summary.index]
    if missing:
        raise ValueError(
            "DUIDs missing from the registration list or DUDETAILSUMMARY: " + ", ".join(missing)
        )

    rows = []
    for duid in wanted:
        r = reg.loc[duid]
        s = summary.loc[duid]
        d = detail.loc[duid] if duid in detail.index else None
        registered = _number(r.get("Reg Cap generation (MW)"))
        maximum = _number(r.get("Max Cap generation (MW)"))
        if registered is None and d is not None:
            registered = _number(d.get("REGISTEREDCAPACITY"))
        if maximum is None and d is not None:
            maximum = _number(d.get("MAXCAPACITY"))
        rows.append(
            {
                "duid": duid,
                "station_name": str(r.get("Station Name", "")).strip(),
                "station_id": str(s.get("STATIONID", "")).strip(),
                "region_id": str(s.get("REGIONID", "")).strip(),
                "fuel": _fuel(r.get("Fuel Source - Primary")),
                "technology": str(r.get("Technology Type - Descriptor", "")).strip(),
                "classification": str(r.get("Classification", "")).strip(),
                "schedule_type": str(s.get("SCHEDULE_TYPE", "")).strip(),
                "dispatch_type": str(s.get("DISPATCHTYPE", "")).strip(),
                "connection_point_id": str(s.get("CONNECTIONPOINTID", "")).strip(),
                "registered_capacity_mw": registered,
                "max_capacity_mw": maximum,
                "registration_list_file": registration_list_file,
                "standing_data_file": standing_data_file,
            }
        )
    return pd.DataFrame(rows, columns=REGISTRY_COLUMNS)


def build_site_registry_from_files(
    duids: Iterable[str],
    registration_xlsx: str | Path,
    dudetailsummary_zip: str | Path,
    dudetail_zip: str | Path,
) -> pd.DataFrame:
    """File-path wrapper used by ``scripts/build_site_registry.py``."""
    return build_site_registry(
        duids,
        registration=read_registration_list(registration_xlsx),
        dudetailsummary=read_single_table_zip(dudetailsummary_zip),
        dudetail=read_single_table_zip(dudetail_zip),
        registration_list_file=Path(registration_xlsx).name,
        standing_data_file=Path(dudetailsummary_zip).name,
    )
