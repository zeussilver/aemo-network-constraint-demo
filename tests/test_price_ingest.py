from __future__ import annotations

import csv
import gzip
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from src.aemo_constraint_demo import price_ingest
from src.aemo_constraint_demo.nemweb_stream import NEM_TZ
from tests.test_nemweb_stream import aemo_csv, write_zip, zip_bytes

PRICE_COLUMNS = [
    "SETTLEMENTDATE", "RUNNO", "REGIONID", "DISPATCHINTERVAL", "INTERVENTION",
    "RRP", "EEP", "ROP", "APCFLAG", "MARKETSUSPENDEDFLAG",
]
REGIONSUM_COLUMNS = [
    "SETTLEMENTDATE", "RUNNO", "REGIONID", "DISPATCHINTERVAL", "INTERVENTION",
    "TOTALDEMAND", "AVAILABLEGENERATION", "SS_SOLAR_UIGF",
]
PREDISPATCH_COLUMNS = [
    "PREDISPATCHSEQNO", "RUNNO", "REGIONID", "PERIODID", "INTERVENTION", "RRP", "DATETIME",
]
# AEMO's real header lists RRPEEP29+ after DATETIME, so the order is deliberately mixed.
SENSITIVITY_COLUMNS = [
    "PREDISPATCHSEQNO", "RUNNO", "REGIONID", "PERIODID", "INTERVENTION",
    "RRPEEP1", "RRPEEP2", "RRPEEP10", "DATETIME", "RRPEEP3",
]


def nem(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=NEM_TZ)


def read_csv_gz(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt", newline="") as handle:
        return list(csv.reader(handle))


def price_zip(tmp_path: Path) -> Path:
    rows = [
        # the pricing run (INTERVENTION 0) is the one that sets the price
        ["2026/08/01 00:05:00", "1", "NSW1", "20260731241", "0", "89.9", "0", "89.9", "0", "0"],
        ["2026/08/01 00:05:00", "1", "NSW1", "20260731241", "1", "15000", "0", "15000", "0", "0"],
        ["2026/08/01 00:10:00", "1", "NSW1", "20260731242", "0", "-45.5", "0", "-45.5", "0", "0"],
        ["2026/08/01 00:05:00", "1", "QLD1", "20260731241", "0", "70.1", "0", "70.1", "0", "0"],
    ]
    return write_zip(
        tmp_path / "price.zip", {"p.CSV": aemo_csv(("DISPATCH,PRICE", PRICE_COLUMNS, rows))}
    )


def test_price_rows_take_the_pricing_run_and_only_wanted_regions(tmp_path: Path) -> None:
    rows, dropped = price_ingest.price_rows(price_zip(tmp_path), ["NSW1"])

    assert dropped == 1
    assert [row[0] for row in rows] == [
        nem("2026-08-01 00:05").isoformat(),
        nem("2026-08-01 00:10").isoformat(),
    ]
    assert [row[2] for row in rows] == [89.9, -45.5]  # not the 15000 intervention run
    assert {row[1] for row in rows} == {"NSW1"}


def test_demand_rows_take_the_physical_run_and_tolerate_missing_columns(tmp_path: Path) -> None:
    rows = [
        ["2026/08/01 00:05:00", "1", "SA1", "20260731241", "0", "1200", "2500", "300"],
        ["2026/08/01 00:05:00", "1", "SA1", "20260731241", "1", "1210", "2510", "305"],
    ]
    path = write_zip(
        tmp_path / "rs.zip", {"r.CSV": aemo_csv(("DISPATCH,REGIONSUM", REGIONSUM_COLUMNS, rows))}
    )

    parsed, dropped = price_ingest.demand_rows(path, ["SA1"])

    assert dropped == 1
    assert len(parsed) == 1
    settlement, region, *values = parsed[0]
    assert (settlement, region) == (nem("2026-08-01 00:05").isoformat(), "SA1")
    assert values[0] == 1210.0  # TOTALDEMAND from the intervention run
    by_column = dict(zip(price_ingest.DEMAND_COLUMNS, values))
    assert by_column["SS_SOLAR_UIGF"] == 305.0
    assert by_column["SS_WIND_UIGF"] is None  # column absent from this file


def test_predispatch_rows_carry_the_target_interval(tmp_path: Path) -> None:
    rows = [
        ["2026080130", "1", "NSW1", "1", "0", "95.5", "2026/08/01 15:00:00"],
        ["2026080130", "1", "VIC1", "1", "0", "88.0", "2026/08/01 15:00:00"],
    ]
    path = write_zip(
        tmp_path / "pd.zip",
        {"pd.CSV": aemo_csv(("PREDISPATCH,REGION_PRICES", PREDISPATCH_COLUMNS, rows))},
    )

    parsed, _ = price_ingest.predispatch_rows(path, ["NSW1"])

    assert parsed == [("2026080130", nem("2026-08-01 15:00").isoformat(), "NSW1", 1, 95.5)]


def test_scenario_columns_are_ordered_by_number() -> None:
    row = {"RRPEEP10": "", "RRPEEP2": "", "RRPEEP1": "", "DATETIME": "", "REGIONID": ""}

    assert price_ingest.scenario_columns(row) == ["RRPEEP1", "RRPEEP2", "RRPEEP10"]


def test_quantile_interpolates_between_neighbours() -> None:
    values = [0.0, 10.0, 20.0, 30.0]

    assert price_ingest.quantile(values, 0.0) == 0.0
    assert price_ingest.quantile(values, 1.0) == 30.0
    assert price_ingest.quantile(values, 0.5) == 15.0


def test_summarise_scenarios_measures_spread_and_horizon_from_publication() -> None:
    published = nem("2026-08-01 14:00")
    row = {
        "PREDISPATCHSEQNO": "2026080130",
        "REGIONID": "NSW1",
        "PERIODID": "4",
        "DATETIME": "2026/08/01 16:00:00",
        "RRPEEP1": "100",
        "RRPEEP2": "-20",
        "RRPEEP3": "400",
        "RRPEEP10": "",  # a scenario AEMO left empty is ignored, not read as zero
    }

    summary = price_ingest.summarise_scenarios(published, row)
    named = dict(zip(price_ingest.SUMMARY_COLUMNS, summary))

    assert named["published"] == published.isoformat()
    assert named["datetime"] == nem("2026-08-01 16:00").isoformat()
    assert named["horizon_min"] == 120
    assert named["n_scenarios"] == 3
    assert (named["min"], named["max"]) == (-20.0, 400.0)
    assert named["p50"] == 100.0
    assert named["share_negative"] == pytest.approx(1 / 3, abs=1e-5)
    assert named["share_above_300"] == pytest.approx(1 / 3, abs=1e-5)
    assert named["scenario_1"] == 100.0  # the base-case candidate, kept as published


def test_summarise_scenarios_skips_rows_with_no_usable_scenario() -> None:
    row = {"DATETIME": "2026/08/01 16:00:00", "REGIONID": "NSW1", "RRPEEP1": ""}

    assert price_ingest.summarise_scenarios(None, row) is None


def sensitivity_archive(tmp_path: Path) -> Path:
    def run_csv(seqno: str, target: str, nsw: list[str], vic: list[str]) -> str:
        return aemo_csv(
            (
                "PREDISPATCH,PRICESENSITIVITIES",
                SENSITIVITY_COLUMNS,
                [
                    [seqno, "1", "NSW1", "1", "0", *nsw[:3], target, nsw[3]],
                    [seqno, "1", "VIC1", "1", "0", *vic[:3], target, vic[3]],
                ],
            )
        )

    members = {
        "PUBLIC_PREDISPATCH_SENSITIVITIES_20260801140000_0000000001.zip": zip_bytes(
            {"a.CSV": run_csv("2026080129", "2026/08/01 16:00:00", ["100", "90", "110", "95"], ["80", "70", "90", "75"])}
        ),
        "PUBLIC_PREDISPATCH_SENSITIVITIES_20260801143000_0000000002.zip": zip_bytes(
            {"b.CSV": run_csv("2026080130", "2026/08/01 16:00:00", ["120", "110", "130", "115"], ["85", "75", "95", "80"])}
        ),
    }
    return write_zip(tmp_path / "PUBLIC_PREDISPATCH_SENSITIVITIES_20260801_20260807.zip", members)


def test_iter_sensitivity_rows_yields_each_run_with_its_publication_time(tmp_path: Path) -> None:
    pairs = list(price_ingest.iter_sensitivity_rows(sensitivity_archive(tmp_path), ["NSW1"]))

    assert [published for published, _ in pairs] == [
        nem("2026-08-01 14:00"),
        nem("2026-08-01 14:30"),
    ]
    assert {row["REGIONID"] for _, row in pairs} == {"NSW1"}


def sensitivity_config(tmp_path: Path, **overrides) -> price_ingest.IngestConfig:
    defaults = dict(
        out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        start="2026-08",
        end="2026-08",
        regions=("NSW1", "VIC1"),
        keep_archives=True,
    )
    defaults.update(overrides)
    return price_ingest.IngestConfig(**defaults)


def patch_archive(monkeypatch: pytest.MonkeyPatch, archive: Path) -> None:
    weekly = price_ingest.WeeklyArchive(
        name=archive.name,
        url=f"https://example.invalid/{archive.name}",
        start=nem("2026-08-01 00:00"),
        end=nem("2026-08-08 00:00"),
    )
    monkeypatch.setattr(price_ingest, "list_sensitivity_archives", lambda *_: [weekly])
    monkeypatch.setattr(price_ingest, "download_file", lambda url, cache_dir: archive)


def test_ingest_sensitivities_writes_a_summary_and_skips_finished_weeks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = sensitivity_archive(tmp_path)
    patch_archive(monkeypatch, archive)
    config = sensitivity_config(tmp_path, raw_regions=("NSW1",))

    first = price_ingest.ingest_sensitivities(config)

    summary = config.out_dir / "sensitivity_summary_20260801_20260807.csv.gz"
    raw = config.out_dir / "sensitivity_scenarios_20260801_20260807.csv.gz"
    assert first.files == 1 and first.rows == 4  # two runs x two regions
    header, *lines = read_csv_gz(summary)
    assert header == list(price_ingest.SUMMARY_COLUMNS)
    assert len(lines) == 4
    raw_header, *raw_lines = read_csv_gz(raw)
    assert raw_header[: len(price_ingest.RAW_KEY_COLUMNS)] == list(price_ingest.RAW_KEY_COLUMNS)
    assert raw_header[len(price_ingest.RAW_KEY_COLUMNS) :] == ["rrpeep1", "rrpeep2", "rrpeep3", "rrpeep10"]
    assert len(raw_lines) == 2  # NSW1 only

    manifest = json.loads((config.out_dir / "manifest.json").read_text())
    entry = manifest[summary.name]
    assert entry["rows"] == 4 and entry["raw_rows"] == 2
    assert entry["first"] == nem("2026-08-01 16:00").isoformat()

    second = price_ingest.ingest_sensitivities(config)
    assert (second.files, second.skipped) == (0, 1)


def test_ingest_sensitivities_deletes_the_archive_unless_asked_to_keep_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = sensitivity_archive(tmp_path)
    patch_archive(monkeypatch, archive)

    price_ingest.ingest_sensitivities(sensitivity_config(tmp_path, keep_archives=False))

    assert not archive.exists()


def test_ingest_monthly_writes_one_file_per_month_then_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = price_zip(tmp_path)
    monkeypatch.setattr(
        price_ingest, "mmsdm_table_url", lambda table, year, month: f"https://example.invalid/{table}"
    )
    monkeypatch.setattr(price_ingest, "download_file", lambda url, cache_dir: archive)
    config = price_ingest.IngestConfig(
        out_dir=tmp_path / "out", cache_dir=tmp_path / "cache",
        start="2026-08", end="2026-08", regions=("NSW1",),
    )

    result = price_ingest.ingest_monthly(price_ingest.MONTHLY_SOURCES["actuals"], config)

    output = config.out_dir / "actual_price_2026-08.csv.gz"
    assert (result.files, result.rows) == (1, 2)
    header, *lines = read_csv_gz(output)
    assert header == ["settlementdate", "regionid", "rrp", "rop", "apcflag", "marketsuspendedflag"]
    assert lines[0][:3] == [nem("2026-08-01 00:05").isoformat(), "NSW1", "89.9"]

    again = price_ingest.ingest_monthly(price_ingest.MONTHLY_SOURCES["actuals"], config)
    assert (again.files, again.skipped) == (0, 1)


def test_a_failed_write_leaves_no_output_behind(tmp_path: Path) -> None:
    path = tmp_path / "out" / "broken.csv.gz"
    with pytest.raises(RuntimeError):
        with price_ingest.CsvGzWriter(path, ("a",)) as writer:
            writer.write(("1",))
            raise RuntimeError("interrupted")

    assert not path.exists()
    assert not path.with_suffix(path.suffix + ".part").exists()


def test_months_between_spans_a_year_end() -> None:
    assert price_ingest.months_between("2025-11", "2026-02") == [
        (2025, 11), (2025, 12), (2026, 1), (2026, 2)
    ]
    with pytest.raises(ValueError):
        price_ingest.months_between("2026-02", "2025-11")
    with pytest.raises(ValueError):
        price_ingest.parse_month("2026/02")


def test_run_published_at_reads_the_file_name_stamp() -> None:
    name = "PUBLIC_PREDISPATCH_SENSITIVITIES_20251015004120_0000000484880595.zip"

    assert price_ingest.run_published_at(name) == nem("2025-10-15 00:41").replace(second=20)
    assert price_ingest.run_published_at("no_stamp.zip") is None


def test_list_sensitivity_archives_keeps_weeks_overlapping_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [
        "PUBLIC_PREDISPATCH_SENSITIVITIES_20250924_20250930.zip",  # before
        "PUBLIC_PREDISPATCH_SENSITIVITIES_20250929_20251005.zip",  # overlaps the start
        "PUBLIC_PREDISPATCH_SENSITIVITIES_20251008_20251014.zip",  # inside
        "PUBLIC_PREDISPATCH_SENSITIVITIES_20251105_20251111.zip",  # after
    ]
    monkeypatch.setattr(
        price_ingest, "list_zip_links", lambda url: [f"https://example.invalid/{n}" for n in names]
    )

    found = price_ingest.list_sensitivity_archives("2025-10", "2025-10")

    assert [archive.name for archive in found] == names[1:3]
