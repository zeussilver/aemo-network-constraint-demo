"""Streaming reader for AEMO multi-table CSV zips.

``aemo_client.parse_multitable_zip`` loads every row of a table into a DataFrame, which is
fine for Next Day Dispatch (~122 MB unzipped) but not for Next_Day_Intermittent_DS
(~337 MB unzipped, ~2.1 million rows a day). This reader walks the CSV row by row, keeps
only the requested tables and DUIDs, and never holds more than one row in memory.

AEMO CSV layout: ``C`` rows are comments, ``I`` rows carry a table header
(``I,<report>,<table>,<version>,<columns...>``) and ``D`` rows carry data in the same
shape. Archive zips (e.g. Dispatch_SCADA daily) nest one zip per 5-minute file.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

NEM_TZ = timezone(timedelta(hours=10), name="NEM")
MAX_NESTING = 4


def parse_nem_time(value: str) -> datetime | None:
    """Parse an AEMO timestamp (``2026/10/17 04:05:00``) as NEM time (fixed UTC+10)."""
    value = value.strip().strip('"')
    if not value:
        return None
    return datetime.strptime(value, "%Y/%m/%d %H:%M:%S").replace(tzinfo=NEM_TZ)


def parse_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def parse_int(value: str) -> int | None:
    value = value.strip()
    return int(float(value)) if value else None


def _iter_csv_members(zf: zipfile.ZipFile, depth: int = 0) -> Iterator[io.TextIOBase]:
    if depth > MAX_NESTING:
        raise RuntimeError("Nested ZIP depth exceeds safety limit.")
    for name in zf.namelist():
        lower = name.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(zf.read(name))) as inner:
                yield from _iter_csv_members(inner, depth + 1)
        elif lower.endswith(".csv"):
            with zf.open(name) as raw:
                yield io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")


def iter_table_rows(
    path: str | Path | io.IOBase,
    tables: Iterable[str],
    duids: Iterable[str] | None = None,
) -> Iterator[tuple[str, dict[str, str]]]:
    """Yield ``(table, row)`` for wanted tables, optionally only for the given DUIDs.

    ``tables`` are ``REPORT,TABLE`` keys, e.g. ``"DISPATCH,UNIT_SCADA"``. Rows of a table
    without a ``DUID`` column are never filtered by DUID. ``path`` is a zip on disk or an
    in-memory one (e.g. a member of an archive zip opened as ``io.BytesIO``).
    """
    wanted = {table.upper() for table in tables}
    duid_filter = {duid.upper() for duid in duids} if duids is not None else None
    with zipfile.ZipFile(path) as zf:
        for text in _iter_csv_members(zf):
            headers: dict[str, list[str]] = {}
            duid_index: dict[str, int | None] = {}
            for row in csv.reader(text):
                if len(row) < 4 or row[0] not in ("I", "D"):
                    continue
                key = f"{row[1]},{row[2]}".upper()
                if key not in wanted:
                    continue
                if row[0] == "I":
                    columns = [column.strip().upper() for column in row[4:]]
                    headers[key] = columns
                    duid_index[key] = columns.index("DUID") if "DUID" in columns else None
                    continue
                columns = headers.get(key)
                if columns is None:
                    continue  # data before its header: malformed, skip
                values = row[4:]
                index = duid_index[key]
                if duid_filter is not None and index is not None:
                    if index >= len(values) or values[index].strip().upper() not in duid_filter:
                        continue
                if len(values) < len(columns):
                    values = values + [""] * (len(columns) - len(values))
                yield key, dict(zip(columns, values))
