#!/usr/bin/env python
"""Data checks on the price lane's files, written to a JSON file the docs quote.

- Coverage: files, rows and the span each source covers.
- Completeness: every region should have one 5-minute price for every interval between
  the first and the last one loaded; gaps and duplicates are counted.
- Price profile: where each region's prices sat, including the negative and high-price
  tails a forecast has to get right.
- Sensitivity coverage: runs per day, scenarios per row and the horizon they reach.
- Scenario alignment: AEMO publishes 43 scenario prices without saying which one is the
  unchanged case. The monthly archive keeps the published forecast price for period 1 of
  every run, so each scenario column can be compared against it: whether any column simply
  is that forecast, how close the closest one gets, and whether the scenario range brackets
  the forecast at all. The modelling needs this before it can read the spread as a
  distribution around a forecast.

    python scripts/price_data_checks.py --price-dir data/price --out artifacts/price_data_checks.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

INTERVAL = timedelta(minutes=5)
TOLERANCE = 0.01  # $/MWh; AEMO writes prices to 5 decimal places


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", newline="") as handle:
        yield from csv.DictReader(handle)


def as_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def coverage(price_dir: Path, prefix: str) -> dict:
    files = sorted(price_dir.glob(f"{prefix}_*.csv.gz"))
    rows = 0
    first = last = None
    regions: set[str] = set()
    for path in files:
        for row in read_rows(path):
            rows += 1
            moment = row.get("settlementdate") or row.get("datetime") or ""
            if moment:
                first = moment if first is None else min(first, moment)
                last = moment if last is None else max(last, moment)
            regions.add(row.get("regionid", ""))
    return {
        "files": len(files),
        "rows": rows,
        "first": first,
        "last": last,
        "regions": sorted(regions),
    }


def actuals_completeness(price_dir: Path) -> dict:
    seen: dict[str, set[str]] = defaultdict(set)
    duplicates = Counter()
    prices: dict[str, list[float]] = defaultdict(list)
    for path in sorted(price_dir.glob("actual_price_*.csv.gz")):
        for row in read_rows(path):
            region = row["regionid"]
            stamp = row["settlementdate"]
            if stamp in seen[region]:
                duplicates[region] += 1
            seen[region].add(stamp)
            price = as_float(row.get("rrp"))
            if price is not None:
                prices[region].append(price)

    report = {}
    for region, stamps in sorted(seen.items()):
        moments = sorted(datetime.fromisoformat(stamp) for stamp in stamps)
        expected = int((moments[-1] - moments[0]) / INTERVAL) + 1
        values = sorted(prices[region])
        report[region] = {
            "intervals": len(moments),
            "expected_intervals": expected,
            "missing_intervals": expected - len(moments),
            "duplicate_rows": duplicates[region],
            "first": moments[0].isoformat(),
            "last": moments[-1].isoformat(),
            "rrp_min": values[0],
            "rrp_median": statistics.median(values),
            "rrp_mean": round(statistics.fmean(values), 4),
            "rrp_max": values[-1],
            "share_negative": round(sum(1 for v in values if v < 0) / len(values), 5),
            "share_above_300": round(sum(1 for v in values if v > 300) / len(values), 5),
            "share_above_5000": round(sum(1 for v in values if v > 5000) / len(values), 6),
        }
    return report


def sensitivity_coverage(price_dir: Path) -> dict:
    files = sorted(price_dir.glob("sensitivity_summary_*.csv.gz"))
    runs_by_day: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    horizons: list[int] = []
    rows = 0
    seen_runs: set[tuple[str, str]] = set()
    spreads: list[float] = []
    for path in files:
        for row in read_rows(path):
            rows += 1
            scenario_counts[row["n_scenarios"]] += 1
            horizon = row.get("horizon_min")
            if horizon:
                horizons.append(int(horizon))
            published = row.get("published", "")
            key = (row["predispatchseqno"], published)
            if key not in seen_runs:
                seen_runs.add(key)
                if published:
                    runs_by_day[published[:10]] += 1
            p10, p90 = as_float(row.get("p10")), as_float(row.get("p90"))
            if p10 is not None and p90 is not None:
                spreads.append(p90 - p10)
    daily = sorted(runs_by_day.values())
    return {
        "files": len(files),
        "rows": rows,
        "runs": len(seen_runs),
        "days": len(runs_by_day),
        "runs_per_day_median": statistics.median(daily) if daily else None,
        "runs_per_day_min": daily[0] if daily else None,
        "scenarios_per_row": dict(scenario_counts.most_common(5)),
        "horizon_min_minutes": min(horizons) if horizons else None,
        "horizon_max_minutes": max(horizons) if horizons else None,
        "p90_minus_p10_median": round(statistics.median(spreads), 4) if spreads else None,
        "p90_minus_p10_p95": round(sorted(spreads)[int(0.95 * (len(spreads) - 1))], 4) if spreads else None,
    }


def load_predispatch_prices(price_dir: Path) -> dict[tuple[str, str], float]:
    """Published forecast price for period 1 of each run, keyed by (run, region)."""
    prices: dict[tuple[str, str], float] = {}
    for path in sorted(price_dir.glob("predispatch_price_*.csv.gz")):
        for row in read_rows(path):
            if int(row.get("periodid", "0") or 0) != 1:
                continue
            price = as_float(row.get("rrp"))
            if price is not None:
                prices[(row["predispatchseqno"], row["regionid"])] = price
    return prices


def scenario_alignment(price_dir: Path) -> dict:
    """Where the published forecast price sits inside the 43 scenario prices.

    AEMO does not say which scenario is the unchanged case. Comparing every scenario
    column against the published forecast price for period 1 of the same run answers two
    questions the modelling depends on: is any column simply that forecast (so the
    scenarios are a distribution around it), and if not, does the scenario range at least
    bracket it, and where in the range does it fall?
    """
    published_prices = load_predispatch_prices(price_dir)
    if not published_prices:
        return {"compared": 0, "note": "no predispatch_price files loaded"}

    matches: Counter[str] = Counter()
    differences: dict[str, list[float]] = defaultdict(list)
    by_month: dict[str, Counter[str]] = defaultdict(Counter)
    month_rows: Counter[str] = Counter()
    positions: list[float] = []
    in_range = 0
    compared = 0
    for path in sorted(price_dir.glob("sensitivity_scenarios_*.csv.gz")):
        for row in read_rows(path):
            if int(row.get("periodid", "0") or 0) != 1:
                continue
            reference = published_prices.get((row["predispatchseqno"], row["regionid"]))
            if reference is None:
                continue
            month = row.get("datetime", "")[:7]
            values = {}
            for column, value in row.items():
                if not column.startswith("rrpeep"):
                    continue
                price = as_float(value)
                if price is None:
                    continue
                values[column] = price
                differences[column].append(abs(price - reference))
                if abs(price - reference) <= TOLERANCE:
                    matches[column] += 1
                    by_month[month][column] += 1
            if not values:
                continue
            compared += 1
            month_rows[month] += 1
            ordered = sorted(values.values())
            if ordered[0] <= reference <= ordered[-1]:
                in_range += 1
            positions.append(sum(1 for value in ordered if value < reference) / len(ordered))

    closest = sorted(
        ((column, statistics.median(diffs)) for column, diffs in differences.items()),
        key=lambda item: item[1],
    )[:5]
    return {
        "compared": compared,
        "exact_match_best": [
            {"column": column, "matches": count, "share": round(count / compared, 5)}
            for column, count in matches.most_common(3)
        ],
        "closest_columns": [
            {"column": column, "median_abs_diff": round(median, 4)} for column, median in closest
        ],
        "best_column_by_month": {
            month: {
                "column": by_month[month].most_common(1)[0][0] if by_month[month] else None,
                "share": round(by_month[month].most_common(1)[0][1] / rows, 4)
                if by_month[month]
                else 0.0,
                "rows": rows,
            }
            for month, rows in sorted(month_rows.items())
        },
        "published_inside_scenario_range": round(in_range / compared, 5) if compared else None,
        "published_position_in_range_median": round(statistics.median(positions), 4) if positions else None,
        "note": (
            "A column that matched on nearly every run would be AEMO's unchanged case. If "
            "none does, the scenarios are a separate solve and the spread has to be anchored "
            "on the published forecast rather than assumed to be centred on it."
        ),
    }


def build_report(price_dir: Path) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "price_dir": str(price_dir),
        "coverage": {
            "actual_price": coverage(price_dir, "actual_price"),
            "region_demand": coverage(price_dir, "region_demand"),
            "predispatch_price": coverage(price_dir, "predispatch_price"),
        },
        "actuals_completeness": actuals_completeness(price_dir),
        "sensitivity_coverage": sensitivity_coverage(price_dir),
        "scenario_alignment": scenario_alignment(price_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--price-dir", default="data/price")
    parser.add_argument("--out", default="artifacts/price_data_checks.json")
    args = parser.parse_args()

    report = build_report(Path(args.price_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
