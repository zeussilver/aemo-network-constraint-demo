from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest

from scripts import price_data_checks


def write_gz(path: Path, columns: list[str], rows: list[list[object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


def price_dir(tmp_path: Path) -> Path:
    out = tmp_path / "price"
    write_gz(
        out / "actual_price_2026-08.csv.gz",
        ["settlementdate", "regionid", "rrp", "rop", "apcflag", "marketsuspendedflag"],
        [
            ["2026-08-01T00:05:00+10:00", "NSW1", "90.0", "90.0", "0", "0"],
            # 00:10 is missing for NSW1
            ["2026-08-01T00:15:00+10:00", "NSW1", "-10.0", "-10.0", "0", "0"],
            ["2026-08-01T00:20:00+10:00", "NSW1", "400.0", "400.0", "0", "0"],
            ["2026-08-01T00:20:00+10:00", "NSW1", "400.0", "400.0", "0", "0"],  # duplicate
            ["2026-08-01T00:05:00+10:00", "SA1", "75.0", "75.0", "0", "0"],
            ["2026-08-01T00:10:00+10:00", "SA1", "76.0", "76.0", "0", "0"],
        ],
    )
    write_gz(
        out / "predispatch_price_2026-08.csv.gz",
        ["predispatchseqno", "datetime", "regionid", "periodid", "rrp"],
        [
            ["2026080129", "2026-08-01T16:00:00+10:00", "NSW1", "1", "100.0"],
            ["2026080130", "2026-08-01T16:30:00+10:00", "NSW1", "1", "120.0"],
            ["2026080130", "2026-08-01T17:00:00+10:00", "NSW1", "2", "130.0"],  # not period 1
        ],
    )
    write_gz(
        out / "sensitivity_summary_20260801_20260807.csv.gz",
        list(price_data_checks_summary_columns()),
        [
            ["2026-08-01T14:00:00+10:00", "2026080129", "2026-08-01T16:00:00+10:00", "NSW1", "1", "120", "43", "80", "150", "100", "10", "90", "110"],
            ["2026-08-01T14:30:00+10:00", "2026080130", "2026-08-01T16:30:00+10:00", "NSW1", "1", "120", "43", "90", "160", "120", "12", "100", "140"],
        ],
    )
    write_gz(
        out / "sensitivity_scenarios_20260801_20260807.csv.gz",
        ["published", "predispatchseqno", "datetime", "regionid", "periodid", "rrpeep1", "rrpeep2", "rrpeep3"],
        [
            # rrpeep3 is the column that reproduces the published forecast price
            ["2026-08-01T14:00:00+10:00", "2026080129", "2026-08-01T16:00:00+10:00", "NSW1", "1", "95.0", "88.0", "100.0"],
            ["2026-08-01T14:30:00+10:00", "2026080130", "2026-08-01T16:30:00+10:00", "NSW1", "1", "111.0", "99.0", "120.0"],
        ],
    )
    return out


def price_data_checks_summary_columns() -> tuple[str, ...]:
    return (
        "published", "predispatchseqno", "datetime", "regionid", "periodid", "horizon_min",
        "n_scenarios", "min", "max", "p50", "std", "p10", "p90",
    )


def test_actuals_completeness_counts_gaps_duplicates_and_tails(tmp_path: Path) -> None:
    report = price_data_checks.actuals_completeness(price_dir(tmp_path))

    nsw = report["NSW1"]
    assert nsw["intervals"] == 3
    assert nsw["expected_intervals"] == 4
    assert nsw["missing_intervals"] == 1
    assert nsw["duplicate_rows"] == 1
    assert nsw["rrp_min"] == -10.0 and nsw["rrp_max"] == 400.0
    assert nsw["share_negative"] == 0.25  # the duplicate row counts in the price profile
    assert report["SA1"]["missing_intervals"] == 0


def test_sensitivity_coverage_reports_runs_horizon_and_spread(tmp_path: Path) -> None:
    report = price_data_checks.sensitivity_coverage(price_dir(tmp_path))

    assert report["files"] == 1
    assert report["rows"] == 2
    assert report["runs"] == 2
    assert report["days"] == 1
    assert report["runs_per_day_median"] == 2
    assert report["scenarios_per_row"] == {"43": 2}
    assert report["horizon_max_minutes"] == 120
    assert report["p90_minus_p10_median"] == 30.0  # (110-90) and (140-100)


def test_scenario_alignment_finds_the_column_that_matches_the_published_price(
    tmp_path: Path,
) -> None:
    report = price_data_checks.scenario_alignment(price_dir(tmp_path))

    assert report["compared"] == 2
    assert report["exact_match_best"][0] == {"column": "rrpeep3", "matches": 2, "share": 1.0}
    assert report["closest_columns"][0] == {"column": "rrpeep3", "median_abs_diff": 0.0}
    assert report["best_column_by_month"] == {
        "2026-08": {"column": "rrpeep3", "share": 1.0, "rows": 2}
    }
    assert report["published_inside_scenario_range"] == 1.0
    # the published price is the highest of the three scenarios in both runs
    assert report["published_position_in_range_median"] == pytest.approx(2 / 3, abs=1e-4)


def test_coverage_summarises_each_family(tmp_path: Path) -> None:
    out = price_dir(tmp_path)

    actuals = price_data_checks.coverage(out, "actual_price")
    predispatch = price_data_checks.coverage(out, "predispatch_price")

    assert actuals["files"] == 1 and actuals["rows"] == 6
    assert actuals["regions"] == ["NSW1", "SA1"]
    assert actuals["first"] == "2026-08-01T00:05:00+10:00"
    assert predispatch["rows"] == 3


def test_build_report_runs_end_to_end(tmp_path: Path) -> None:
    report = price_data_checks.build_report(price_dir(tmp_path))

    assert set(report) >= {
        "coverage", "actuals_completeness", "sensitivity_coverage", "scenario_alignment"
    }
    assert report["scenario_alignment"]["closest_columns"][0]["column"] == "rrpeep3"
