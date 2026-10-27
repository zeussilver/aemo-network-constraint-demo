"""Load NEMWeb regional price data for the price-forecasting lane.

The forecasting question here is different from the constraint model's: how will the
regional reference price (RRP) behave over the next few hours, and how wide is the
uncertainty around it? Four sources answer that, each written as a gzipped CSV under
``--out-dir`` so the modelling code can read a year without touching NEMWeb again:

- ``actuals``: MMSDM monthly ``DISPATCH,PRICE``. The 5-minute RRP per region, the target.
- ``demand``: MMSDM monthly ``DISPATCH,REGIONSUM``. Demand, available generation and the
  semi-scheduled solar/wind availability behind each interval, used to label market
  regimes rather than to predict.
- ``predispatch``: MMSDM monthly ``PREDISPATCH,REGION_PRICES``. AEMO's own forecast price,
  the benchmark any model here has to beat. The monthly archive keeps **only period 1** of
  each run, so this source gives one horizon, not the curve; it is enough to identify which
  sensitivity scenario is the base case (see ``sensitivities``).
- ``sensitivities``: weekly ``Reports/Archive/Predispatch_Sensitivities`` zips. Each
  half-hourly predispatch run publishes, per region and per period out to ~34 hours, the
  price under 43 demand scenarios (``RRPEEP1``..``RRPEEP43``). The spread across those
  scenarios is AEMO's own published view of price uncertainty, so this is the lane's main
  input. The 43 values are summarised per row (quantiles, spread, tail shares); the raw
  scenarios are kept only for the regions and months asked for with ``--raw-regions``.

Two choices worth knowing when reading the output:

- **Intervention runs.** When AEMO intervenes, the dispatch interval is solved twice. Price
  rows are taken from the pricing run (``INTERVENTION = 0``) and ``REGIONSUM`` rows from the
  physical run (``INTERVENTION = 1``) when one exists, which is the same rule
  ``ops_ingest`` uses for dispatch targets. The manifest records how many rows were dropped.
- **Publication time.** A predispatch run's rows say which interval they are about, not when
  they were published. The publication timestamp in each nested zip's file name is kept as
  ``published``, and ``horizon_min`` is measured from it, so a backtest can only ever use a
  forecast that existed at the time.

Every output file is written once and skipped on re-runs (``--overwrite`` to force), so the
backfill can be stopped and resumed.

    python -m src.aemo_constraint_demo.price_ingest --from 2025-10 --to 2026-09
    python -m src.aemo_constraint_demo.price_ingest --sources sensitivities \\
        --from 2026-08 --to 2026-09 --raw-regions NSW1
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence
from urllib.parse import unquote, urlparse

from .aemo_client import NEMWEB_BASE, download_file, list_zip_links
from .nemweb_stream import NEM_TZ, iter_table_rows, parse_nem_time

log = logging.getLogger("price_ingest")

SOURCES = ("actuals", "demand", "predispatch", "sensitivities")
REGIONS = ("NSW1", "QLD1", "SA1", "TAS1", "VIC1")

MMSDM_BASE = f"{NEMWEB_BASE}/Data_Archive/Wholesale_Electricity/MMSDM"
SENSITIVITIES_ARCHIVE = f"{NEMWEB_BASE}/Reports/Archive/Predispatch_Sensitivities/"

DISPATCH_PRICE = "DISPATCH,PRICE"
DISPATCH_REGIONSUM = "DISPATCH,REGIONSUM"
PREDISPATCH_PRICE = "PREDISPATCH,REGION_PRICES"
PRICE_SENSITIVITIES = "PREDISPATCH,PRICESENSITIVITIES"

# Columns kept from DISPATCH,REGIONSUM. AEMO adds columns over time, so missing ones are
# written empty rather than failing the load.
DEMAND_COLUMNS = (
    "TOTALDEMAND",
    "DEMANDFORECAST",
    "AVAILABLEGENERATION",
    "DISPATCHABLEGENERATION",
    "NETINTERCHANGE",
    "SS_SOLAR_UIGF",
    "SS_WIND_UIGF",
    "SS_SOLAR_CLEAREDMW",
    "SS_WIND_CLEAREDMW",
)

QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
HIGH_PRICE = 300.0  # a volatility marker, well above a normal cap-free interval


# ---------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------


@dataclass
class IngestConfig:
    out_dir: Path = Path("data/price")
    cache_dir: Path = Path("data/cache/price")
    start: str = "2025-10"
    end: str = "2026-09"
    regions: tuple[str, ...] = REGIONS
    raw_regions: tuple[str, ...] = ()
    keep_archives: bool = False
    overwrite: bool = False
    max_files: int | None = None


@dataclass
class SourceResult:
    source: str
    files: int = 0
    rows: int = 0
    skipped: int = 0
    status: str = "ok"
    error: str | None = None
    outputs: list[str] = field(default_factory=list)


def months_between(start: str, end: str) -> list[tuple[int, int]]:
    """Inclusive list of ``(year, month)`` for ``YYYY-MM`` bounds."""
    first, last = parse_month(start), parse_month(end)
    if last < first:
        raise ValueError(f"--to ({end}) is before --from ({start}).")
    out, cursor = [], first
    while cursor <= last:
        out.append(cursor)
        cursor = (cursor[0] + 1, 1) if cursor[1] == 12 else (cursor[0], cursor[1] + 1)
    return out


def parse_month(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
    if not match:
        raise ValueError(f"Expected a month as YYYY-MM, got {value!r}.")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"Month out of range in {value!r}.")
    return year, month


def with_retries(action: Callable[[], object], what: str, attempts: int = 3, delay: float = 2.0):
    """NEMWeb occasionally drops a request; retry a few times before giving up."""
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - re-raised after the last attempt
            if attempt == attempts:
                raise
            log.warning("%s failed (attempt %d/%d): %s", what, attempt, attempts, exc)
            time.sleep(delay * attempt)
    raise AssertionError("unreachable")


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile of an already sorted sequence."""
    if not values:
        raise ValueError("quantile of an empty sequence")
    if len(values) == 1:
        return values[0]
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    weight = position - low
    return values[low] * (1 - weight) + values[high] * weight


def as_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def iso(moment: datetime | None) -> str:
    return moment.isoformat() if moment is not None else ""


# ---------------------------------------------------------------------------------------
# Output files and the manifest
# ---------------------------------------------------------------------------------------


class CsvGzWriter:
    """Write rows to ``<path>.part`` and move it into place only once the file is complete.

    A half-written output would otherwise be skipped as "already done" on the next run.
    """

    def __init__(self, path: Path, columns: Sequence[str]) -> None:
        self.path = path
        self.columns = list(columns)
        self.rows = 0
        self._tmp = path.with_suffix(path.suffix + ".part")

    def __enter__(self) -> "CsvGzWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = gzip.open(self._tmp, "wt", newline="", encoding="utf-8")
        self._writer = csv.writer(self._handle)
        self._writer.writerow(self.columns)
        return self

    def write(self, row: Sequence[object]) -> None:
        self._writer.writerow(row)
        self.rows += 1

    def __exit__(self, exc_type, exc, tb) -> None:
        self._handle.close()
        if exc_type is None:
            self._tmp.replace(self.path)
        else:
            self._tmp.unlink(missing_ok=True)


def update_manifest(out_dir: Path, entry: dict) -> None:
    """Record one output file in ``manifest.json`` (what it holds and where it came from)."""
    path = out_dir / "manifest.json"
    manifest: dict[str, dict] = {}
    if path.exists():
        try:
            manifest = json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("manifest.json was unreadable and is being rewritten")
    manifest[entry["output"]] = entry
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------------------
# Remote files
# ---------------------------------------------------------------------------------------


def mmsdm_table_url(table: str, year: int, month: int) -> str:
    """Find a monthly MMSDM table zip by reading the month's directory listing.

    The archive file name contains ``#`` separators, and which percent-encoding the
    endpoint accepts has changed before, so the link is taken from the listing rather than
    rebuilt from a template.
    """
    directory = f"{MMSDM_BASE}/{year}/MMSDM_{year}_{month:02d}/MMSDM_Historical_Data_SQLLoader/DATA/"
    links = with_retries(lambda: list_zip_links(directory), f"listing {directory}")
    table = table.upper()
    wanted = (f"#{table}#FILE01#", f"PUBLIC_DVD_{table}_")
    for url in links:
        name = unquote(Path(urlparse(url).path).name).upper()
        if any(marker in name for marker in wanted):
            return url
    raise RuntimeError(f"No monthly archive for {table} in {directory}")


@dataclass(frozen=True)
class WeeklyArchive:
    name: str
    url: str
    start: datetime
    end: datetime


_WEEK_STAMP = re.compile(r"_(\d{8})_(\d{8})\.zip$", re.IGNORECASE)
_RUN_STAMP = re.compile(r"_(\d{14})_", re.IGNORECASE)


def list_sensitivity_archives(start: str, end: str) -> list[WeeklyArchive]:
    """Weekly sensitivity archives overlapping the ``YYYY-MM`` window, oldest first."""
    first_year, first_month = parse_month(start)
    last_year, last_month = parse_month(end)
    window_start = datetime(first_year, first_month, 1, tzinfo=NEM_TZ)
    window_end = datetime(
        last_year + (1 if last_month == 12 else 0),
        1 if last_month == 12 else last_month + 1,
        1,
        tzinfo=NEM_TZ,
    )
    links = with_retries(
        lambda: list_zip_links(SENSITIVITIES_ARCHIVE), f"listing {SENSITIVITIES_ARCHIVE}"
    )
    archives = []
    for url in links:
        name = unquote(Path(urlparse(url).path).name)
        match = _WEEK_STAMP.search(name)
        if not match:
            continue
        begin = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=NEM_TZ)
        finish = datetime.strptime(match.group(2), "%Y%m%d").replace(tzinfo=NEM_TZ) + timedelta(days=1)
        if finish > window_start and begin < window_end:
            archives.append(WeeklyArchive(name=name, url=url, start=begin, end=finish))
    return sorted(archives, key=lambda item: item.start)


def run_published_at(member_name: str) -> datetime | None:
    """Publication time from a run's file name, e.g. ``..._20251015004120_000...zip``."""
    match = _RUN_STAMP.search(member_name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=NEM_TZ)


# ---------------------------------------------------------------------------------------
# Parsers (pure: a downloaded file in, rows out)
# ---------------------------------------------------------------------------------------


def _pick_by_intervention(
    rows: Iterable[dict[str, str]],
    key_columns: Sequence[str],
    prefer_intervention: str,
) -> tuple[list[dict[str, str]], int]:
    """Keep one row per key, preferring the wanted run; return the rows and how many were dropped.

    Price comes from the pricing run (``INTERVENTION = 0``) and physical quantities from the
    intervention run when AEMO solved the interval twice.
    """
    chosen: dict[tuple[str, ...], dict[str, str]] = {}
    dropped = 0
    for row in rows:
        key = tuple(row.get(column, "") for column in key_columns)
        current = chosen.get(key)
        if current is None:
            chosen[key] = row
            continue
        dropped += 1
        if row.get("INTERVENTION", "0").strip() == prefer_intervention:
            chosen[key] = row
    return list(chosen.values()), dropped


def price_rows(path: Path, regions: Sequence[str]) -> tuple[list[tuple], int]:
    wanted = {region.upper() for region in regions}
    raw = [
        row
        for _, row in iter_table_rows(path, [DISPATCH_PRICE])
        if row.get("REGIONID", "").strip().upper() in wanted
    ]
    rows, dropped = _pick_by_intervention(raw, ("SETTLEMENTDATE", "REGIONID"), "0")
    out = []
    for row in rows:
        settlement = parse_nem_time(row.get("SETTLEMENTDATE", ""))
        if settlement is None:
            continue
        out.append(
            (
                iso(settlement),
                row.get("REGIONID", "").strip().upper(),
                as_float(row.get("RRP")),
                as_float(row.get("ROP")),
                row.get("APCFLAG", "").strip(),
                row.get("MARKETSUSPENDEDFLAG", "").strip(),
            )
        )
    out.sort(key=lambda item: (item[0], item[1]))
    return out, dropped


def demand_rows(path: Path, regions: Sequence[str]) -> tuple[list[tuple], int]:
    wanted = {region.upper() for region in regions}
    raw = [
        row
        for _, row in iter_table_rows(path, [DISPATCH_REGIONSUM])
        if row.get("REGIONID", "").strip().upper() in wanted
    ]
    rows, dropped = _pick_by_intervention(raw, ("SETTLEMENTDATE", "REGIONID"), "1")
    out = []
    for row in rows:
        settlement = parse_nem_time(row.get("SETTLEMENTDATE", ""))
        if settlement is None:
            continue
        values = [as_float(row.get(column)) for column in DEMAND_COLUMNS]
        out.append((iso(settlement), row.get("REGIONID", "").strip().upper(), *values))
    out.sort(key=lambda item: (item[0], item[1]))
    return out, dropped


def predispatch_rows(path: Path, regions: Sequence[str]) -> tuple[list[tuple], int]:
    wanted = {region.upper() for region in regions}
    raw = [
        row
        for _, row in iter_table_rows(path, [PREDISPATCH_PRICE])
        if row.get("REGIONID", "").strip().upper() in wanted
    ]
    rows, dropped = _pick_by_intervention(
        raw, ("PREDISPATCHSEQNO", "REGIONID", "PERIODID"), "0"
    )
    out = []
    for row in rows:
        target = parse_nem_time(row.get("DATETIME", ""))
        if target is None:
            continue
        out.append(
            (
                row.get("PREDISPATCHSEQNO", "").strip(),
                iso(target),
                row.get("REGIONID", "").strip().upper(),
                int(row.get("PERIODID", "0").strip() or 0),
                as_float(row.get("RRP")),
            )
        )
    out.sort(key=lambda item: (item[0], item[2], item[3]))
    return out, dropped


def scenario_columns(row: dict[str, str]) -> list[str]:
    """``RRPEEP`` columns in scenario order; AEMO's header lists them out of order."""
    numbered = []
    for column in row:
        match = re.fullmatch(r"RRPEEP(\d+)", column)
        if match:
            numbered.append((int(match.group(1)), column))
    return [column for _, column in sorted(numbered)]


SUMMARY_COLUMNS = (
    "published",
    "predispatchseqno",
    "datetime",
    "regionid",
    "periodid",
    "horizon_min",
    "n_scenarios",
    "min",
    "max",
    "mean",
    "std",
    *[f"p{int(q * 100):02d}" for q in QUANTILES],
    "iqr",
    "range",
    "share_negative",
    "share_above_300",
    "scenario_1",
)


def summarise_scenarios(
    published: datetime | None, row: dict[str, str]
) -> tuple | None:
    """One summary row: where the 43 scenario prices sit for this region and period."""
    target = parse_nem_time(row.get("DATETIME", ""))
    if target is None:
        return None
    columns = scenario_columns(row)
    values = [value for value in (as_float(row.get(column)) for column in columns) if value is not None]
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    mean = sum(ordered) / count
    variance = sum((value - mean) ** 2 for value in ordered) / count
    quantiles = [quantile(ordered, q) for q in QUANTILES]
    p25 = quantile(ordered, 0.25)
    p75 = quantile(ordered, 0.75)
    horizon = None
    if published is not None:
        horizon = round((target - published).total_seconds() / 60)
    return (
        iso(published),
        row.get("PREDISPATCHSEQNO", "").strip(),
        iso(target),
        row.get("REGIONID", "").strip().upper(),
        int(row.get("PERIODID", "0").strip() or 0),
        horizon,
        count,
        ordered[0],
        ordered[-1],
        round(mean, 5),
        round(variance**0.5, 5),
        *[round(value, 5) for value in quantiles],
        round(p75 - p25, 5),
        round(ordered[-1] - ordered[0], 5),
        round(sum(1 for value in ordered if value < 0) / count, 5),
        round(sum(1 for value in ordered if value > HIGH_PRICE) / count, 5),
        as_float(row.get(columns[0])) if columns else None,
    )


def iter_sensitivity_rows(
    archive_path: Path, regions: Sequence[str]
) -> Iterator[tuple[datetime | None, dict[str, str]]]:
    """Walk a weekly archive run by run, yielding each run's publication time with its rows."""
    wanted = {region.upper() for region in regions}
    with zipfile.ZipFile(archive_path) as archive:
        for member in sorted(archive.namelist()):
            if not member.lower().endswith(".zip"):
                continue
            published = run_published_at(Path(member).name)
            payload = io.BytesIO(archive.read(member))
            for _, row in iter_table_rows(payload, [PRICE_SENSITIVITIES]):
                if row.get("REGIONID", "").strip().upper() in wanted:
                    yield published, row


# ---------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthlySource:
    name: str
    table: str
    prefix: str
    columns: tuple[str, ...]
    parser: Callable[[Path, Sequence[str]], tuple[list[tuple], int]]
    time_index: int = 0  # which output column holds the interval the row is about


MONTHLY_SOURCES = {
    "actuals": MonthlySource(
        name="actuals",
        table="DISPATCHPRICE",
        prefix="actual_price",
        columns=("settlementdate", "regionid", "rrp", "rop", "apcflag", "marketsuspendedflag"),
        parser=price_rows,
    ),
    "demand": MonthlySource(
        name="demand",
        table="DISPATCHREGIONSUM",
        prefix="region_demand",
        columns=("settlementdate", "regionid", *[column.lower() for column in DEMAND_COLUMNS]),
        parser=demand_rows,
    ),
    "predispatch": MonthlySource(
        name="predispatch",
        table="PREDISPATCHPRICE",
        prefix="predispatch_price",
        columns=("predispatchseqno", "datetime", "regionid", "periodid", "rrp"),
        parser=predispatch_rows,
        time_index=1,
    ),
}


def ingest_monthly(source: MonthlySource, config: IngestConfig) -> SourceResult:
    result = SourceResult(source=source.name)
    months = months_between(config.start, config.end)
    if config.max_files is not None:
        months = months[: config.max_files]
    for year, month in months:
        output = config.out_dir / f"{source.prefix}_{year}-{month:02d}.csv.gz"
        if output.exists() and not config.overwrite:
            result.skipped += 1
            continue
        url = mmsdm_table_url(source.table, year, month)
        path = with_retries(
            lambda: download_file(url, cache_dir=config.cache_dir), f"downloading {url}"
        )
        rows, dropped = source.parser(path, config.regions)
        with CsvGzWriter(output, source.columns) as writer:
            for row in rows:
                writer.write(row)
        result.files += 1
        result.rows += writer.rows
        result.outputs.append(output.name)
        update_manifest(
            config.out_dir,
            {
                "output": output.name,
                "source": source.name,
                "table": source.table,
                "input": Path(urlparse(url).path).name,
                "rows": writer.rows,
                "intervention_rows_dropped": dropped,
                "first": min((row[source.time_index] for row in rows), default=None),
                "last": max((row[source.time_index] for row in rows), default=None),
                "regions": list(config.regions),
                "written_at": datetime.now(NEM_TZ).isoformat(),
            },
        )
        log.info("%s %d-%02d: %d rows -> %s", source.name, year, month, writer.rows, output.name)
    return result


RAW_KEY_COLUMNS = ("published", "predispatchseqno", "datetime", "regionid", "periodid")


def ingest_sensitivities(config: IngestConfig) -> SourceResult:
    result = SourceResult(source="sensitivities")
    archives = list_sensitivity_archives(config.start, config.end)
    if config.max_files is not None:
        archives = archives[: config.max_files]
    if not archives:
        log.warning("No weekly sensitivity archives found for %s..%s", config.start, config.end)
    for archive in archives:
        stem = archive.name.replace("PUBLIC_PREDISPATCH_SENSITIVITIES_", "").replace(".zip", "")
        summary_path = config.out_dir / f"sensitivity_summary_{stem}.csv.gz"
        raw_path = config.out_dir / f"sensitivity_scenarios_{stem}.csv.gz"
        wants_raw = bool(config.raw_regions)
        # Keyed on the summary alone: widening --raw-regions later needs --overwrite.
        if summary_path.exists() and not config.overwrite:
            result.skipped += 1
            continue

        path = with_retries(
            lambda: download_file(archive.url, cache_dir=config.cache_dir),
            f"downloading {archive.name}",
        )
        raw_columns: list[str] | None = None
        first_target = last_target = None
        summary_rows = raw_rows = 0
        try:
            with CsvGzWriter(summary_path, SUMMARY_COLUMNS) as summary:
                raw_writer: CsvGzWriter | None = None
                try:
                    for published, row in iter_sensitivity_rows(path, config.regions):
                        line = summarise_scenarios(published, row)
                        if line is None:
                            continue
                        summary.write(line)
                        summary_rows += 1
                        first_target = min(first_target or line[2], line[2])
                        last_target = max(last_target or line[2], line[2])
                        if not wants_raw:
                            continue
                        if row.get("REGIONID", "").strip().upper() not in {
                            region.upper() for region in config.raw_regions
                        }:
                            continue
                        if raw_writer is None:
                            raw_columns = scenario_columns(row)
                            raw_writer = CsvGzWriter(
                                raw_path, (*RAW_KEY_COLUMNS, *[c.lower() for c in raw_columns])
                            )
                            raw_writer.__enter__()
                        raw_writer.write(
                            (
                                line[0],
                                line[1],
                                line[2],
                                line[3],
                                line[4],
                                *[as_float(row.get(column)) for column in raw_columns or []],
                            )
                        )
                        raw_rows += 1
                finally:
                    if raw_writer is not None:
                        raw_writer.__exit__(None, None, None)
        finally:
            if not config.keep_archives:
                path.unlink(missing_ok=True)

        result.files += 1
        result.rows += summary_rows
        result.outputs.append(summary_path.name)
        update_manifest(
            config.out_dir,
            {
                "output": summary_path.name,
                "source": "sensitivities",
                "table": PRICE_SENSITIVITIES,
                "input": archive.name,
                "rows": summary_rows,
                "raw_output": raw_path.name if raw_rows else None,
                "raw_rows": raw_rows,
                "raw_regions": list(config.raw_regions),
                "first": first_target,
                "last": last_target,
                "regions": list(config.regions),
                "written_at": datetime.now(NEM_TZ).isoformat(),
            },
        )
        log.info("sensitivities %s: %d summary rows, %d raw rows", stem, summary_rows, raw_rows)
    return result


def run(sources: Sequence[str], config: IngestConfig) -> list[SourceResult]:
    results = []
    for source in sources:
        try:
            if source == "sensitivities":
                results.append(ingest_sensitivities(config))
            else:
                results.append(ingest_monthly(MONTHLY_SOURCES[source], config))
        except Exception as exc:  # noqa: BLE001 - one bad source should not stop the rest
            log.exception("source %s failed", source)
            results.append(SourceResult(source=source, status="failed", error=str(exc)))
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load NEMWeb regional price data.")
    parser.add_argument("--sources", default=",".join(SOURCES), help=f"Subset of {SOURCES}.")
    parser.add_argument("--from", dest="start", default="2025-10", help="First month, YYYY-MM.")
    parser.add_argument("--to", dest="end", default="2026-09", help="Last month, YYYY-MM.")
    parser.add_argument("--regions", default=",".join(REGIONS))
    parser.add_argument(
        "--raw-regions",
        default="",
        help="Regions to also keep the 43 raw scenario prices for (default: summary only).",
    )
    parser.add_argument("--out-dir", default="data/price")
    parser.add_argument("--cache-dir", default="data/cache/price")
    parser.add_argument(
        "--keep-archives",
        action="store_true",
        help="Keep downloaded weekly sensitivity zips (~17 MB each) instead of deleting them.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Rewrite outputs that exist.")
    parser.add_argument("--max-files", type=int, default=None, help="Cap months/weeks (smoke runs).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_arg_parser().parse_args(argv)
    sources = [item.strip() for item in args.sources.split(",") if item.strip()]
    unknown = sorted(set(sources) - set(SOURCES))
    if unknown:
        print(f"Unknown source(s): {unknown}. Choose from {SOURCES}.", file=sys.stderr)
        return 2
    config = IngestConfig(
        out_dir=Path(args.out_dir),
        cache_dir=Path(args.cache_dir),
        start=args.start,
        end=args.end,
        regions=tuple(item.strip().upper() for item in args.regions.split(",") if item.strip()),
        raw_regions=tuple(
            item.strip().upper() for item in args.raw_regions.split(",") if item.strip()
        ),
        keep_archives=args.keep_archives,
        overwrite=args.overwrite,
        max_files=args.max_files,
    )
    results = run(sources, config)
    for result in results:
        log.info(
            "%s: %s, %d files, %d rows, %d skipped%s",
            result.source,
            result.status,
            result.files,
            result.rows,
            result.skipped,
            f", error: {result.error}" if result.error else "",
        )
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
