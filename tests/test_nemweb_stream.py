from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.aemo_constraint_demo.nemweb_stream import NEM_TZ, iter_table_rows, parse_nem_time


def aemo_csv(*tables: tuple[str, list[str], list[list[str]]]) -> str:
    """Build an AEMO multi-table CSV: C header, then I/D rows per table, then C footer."""
    lines = ["C,NEMP.WORLD,TEST,AEMO,PUBLIC,2026/10/17,04:00:00,0,TEST,0"]
    for key, columns, rows in tables:
        report, table = key.split(",")
        lines.append(",".join(["I", report, table, "1", *columns]))
        lines += [",".join(["D", report, table, "1", *row]) for row in rows]
    lines.append("C,END OF REPORT,99")
    return "\n".join(lines) + "\n"


def write_zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return path


def zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


SCADA_COLUMNS = ["SETTLEMENTDATE", "DUID", "SCADAVALUE", "LASTCHANGED"]


def test_parse_nem_time_is_fixed_utc_plus_10() -> None:
    parsed = parse_nem_time('"2026/10/17 04:05:00"')
    assert parsed == datetime(2026, 10, 17, 4, 5, tzinfo=NEM_TZ)
    assert parsed.utcoffset() == timedelta(hours=10)
    assert parsed.astimezone(timezone.utc).hour == 18
    assert parse_nem_time("") is None


def test_iter_table_rows_filters_tables_and_duids(tmp_path: Path) -> None:
    csv_text = aemo_csv(
        ("DISPATCH,UNIT_SCADA", SCADA_COLUMNS, [
            ["2026/10/17 00:05:00", "CATHROCK", "41.2", "x"],
            ["2026/10/17 00:05:00", "OTHER1", "9", "x"],
        ]),
        ("DISPATCH,LOCAL_PRICE", ["SETTLEMENTDATE", "DUID", "LOCAL_PRICE_ADJUSTMENT"], [["2026/10/17 00:05:00", "CATHROCK", "0"]]),
    )
    path = write_zip(tmp_path / "scada.zip", {"PUBLIC_DISPATCHSCADA.CSV": csv_text})

    rows = list(iter_table_rows(path, ["DISPATCH,UNIT_SCADA"], ["cathrock"]))

    assert rows == [("DISPATCH,UNIT_SCADA", {"SETTLEMENTDATE": "2026/10/17 00:05:00", "DUID": "CATHROCK", "SCADAVALUE": "41.2", "LASTCHANGED": "x"})]


def test_iter_table_rows_reads_nested_archive_zips(tmp_path: Path) -> None:
    inner = {
        f"PUBLIC_DISPATCHSCADA_20261017000{i}.zip": zip_bytes(
            {"a.CSV": aemo_csv(("DISPATCH,UNIT_SCADA", SCADA_COLUMNS, [[f"2026/10/17 00:0{i}:00", "CATHROCK", str(i), "x"]]))}
        )
        for i in (5, 6)
    }
    path = write_zip(tmp_path / "archive.zip", inner)

    values = [row["SCADAVALUE"] for _, row in iter_table_rows(path, ["DISPATCH,UNIT_SCADA"], None)]

    assert values == ["5", "6"]


def test_iter_table_rows_handles_header_changes_and_short_rows(tmp_path: Path) -> None:
    text = (
        "D,DISPATCH,UNIT_SCADA,1,2026/10/17 00:05:00,CATHROCK,1,x\n"  # data before header: skipped
        "I,DISPATCH,UNIT_SCADA,1,SETTLEMENTDATE,DUID,SCADAVALUE,LASTCHANGED\n"
        "D,DISPATCH,UNIT_SCADA,1,2026/10/17 00:10:00,CATHROCK,2\n"  # short row: padded
    )
    path = write_zip(tmp_path / "odd.zip", {"x.csv": text})

    rows = [row for _, row in iter_table_rows(path, ["DISPATCH,UNIT_SCADA"], ["CATHROCK"])]

    assert rows == [{"SETTLEMENTDATE": "2026/10/17 00:10:00", "DUID": "CATHROCK", "SCADAVALUE": "2", "LASTCHANGED": ""}]
