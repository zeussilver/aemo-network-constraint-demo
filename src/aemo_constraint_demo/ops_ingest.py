"""Load NEMWeb operational data for the ops console's sites into Postgres.

Four sources, each idempotent and safe to run on a schedule:

- ``scada``: DISPATCH_UNIT_SCADA actual MW. Daily ARCHIVE zips for the backfill window,
  then the 5-minute CURRENT files newer than the last archived day (near-live).
- ``dispatch``: Next Day Dispatch DISPATCH,UNIT_SOLUTION (availability, UIGF, semi-dispatch
  cap, cleared MW), published once a day for the previous trading day.
- ``intermittent``: Next_Day_Intermittent_DS. Which forecast dispatch used per interval
  (INTERMITTENT_FORECAST_TRK + its POE50) and, per origin, how many submissions arrived and
  how many were suppressed (INTERMITTENT_DS_RUN/PRED).
- ``forecast``: the repo's constraint-risk model (``forecast_from_bundle``), scored for
  every DUID that has a bundle, stored as a snapshot.

Every NEMWeb file is loaded in one transaction together with its ``ingested_file`` row, so
a file is either fully loaded or not at all, and the next run skips files it has seen.
Each run writes an ``ingest_run`` row per source; the console's freshness checks and the
ingestion alert read those rows and the newest interval in each table.

The Prisma migrations in ``console/prisma`` own the schema; this job only writes rows.

    python -m src.aemo_constraint_demo.ops_ingest --sources scada,dispatch,intermittent,forecast
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence
from urllib.parse import unquote, urlparse

from .aemo_client import NEMWEB_BASE, download_file, list_zip_links
from .nemweb_stream import NEM_TZ, iter_table_rows, parse_float, parse_int, parse_nem_time

log = logging.getLogger("ops_ingest")

SOURCES = ("scada", "dispatch", "intermittent", "forecast")
DEFAULT_MODELS = ("artifacts/cathrock_lead_time_models.joblib",)

SCADA_CURRENT = f"{NEMWEB_BASE}/Reports/CURRENT/Dispatch_SCADA/"
SCADA_ARCHIVE = f"{NEMWEB_BASE}/Reports/ARCHIVE/Dispatch_SCADA/"
DISPATCH_CURRENT = f"{NEMWEB_BASE}/Reports/CURRENT/Next_Day_Dispatch/"
INTERMITTENT_CURRENT = f"{NEMWEB_BASE}/Reports/CURRENT/Next_Day_Intermittent_DS/"

UNIT_SCADA = "DISPATCH,UNIT_SCADA"
UNIT_SOLUTION = "DISPATCH,UNIT_SOLUTION"
DS_RUN = "DEMAND,INTERMITTENT_DS_RUN"
DS_PRED = "DEMAND,INTERMITTENT_DS_PRED"
FORECAST_TRK = "DISPATCH,INTERMITTENT_FORECAST_TRK"


# ---------------------------------------------------------------------------------------
# Remote file listings
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteFile:
    name: str
    url: str
    stamp: datetime  # NEM time: the interval (5-minute files) or day the file covers


_STAMP = re.compile(r"_(\d{8})(\d{4})?(?:_\d+)?\.zip$", re.IGNORECASE)


def file_stamp(name: str) -> datetime | None:
    """Date (and time, for 5-minute files) encoded in a NEMWeb file name, as NEM time."""
    match = _STAMP.search(name)
    if not match:
        return None
    day, hhmm = match.group(1), match.group(2) or "0000"
    return datetime.strptime(day + hhmm, "%Y%m%d%H%M").replace(tzinfo=NEM_TZ)


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


def list_remote(directory_url: str, prefix: str) -> list[RemoteFile]:
    links = with_retries(lambda: list_zip_links(directory_url), f"listing {directory_url}")
    files = []
    for url in links:
        name = unquote(Path(urlparse(url).path).name)
        if not name.upper().startswith(prefix):
            continue
        stamp = file_stamp(name)
        if stamp is not None:
            files.append(RemoteFile(name=name, url=url, stamp=stamp))
    return sorted(files, key=lambda item: item.stamp)


def select_files(files: Sequence[RemoteFile], seen: set[str], since: datetime) -> list[RemoteFile]:
    """Files inside the window that have not been loaded yet, oldest first."""
    return [item for item in files if item.stamp >= since and item.name not in seen]


def select_scada_files(
    archive: Sequence[RemoteFile], current: Sequence[RemoteFile], seen: set[str], since: datetime
) -> list[RemoteFile]:
    """Daily archives for the window, then 5-minute files after the newest archived day.

    An archive file named for day D holds intervals D 00:05 → D+1 00:00, so 5-minute files
    stamped at or before D+1 00:00 are already covered and are skipped.
    """
    days = [item for item in archive if item.stamp >= since]
    covered_until = max((item.stamp + timedelta(days=1) for item in days), default=since)
    recent = [item for item in current if item.stamp > covered_until]
    return select_files(days, seen, since) + select_files(recent, seen, since)


# ---------------------------------------------------------------------------------------
# Parsers (pure: file path in, rows out)
# ---------------------------------------------------------------------------------------


def scada_rows(path: str | Path, duids: Iterable[str]) -> list[tuple]:
    rows = []
    for _, row in iter_table_rows(path, [UNIT_SCADA], duids):
        mw = parse_float(row.get("SCADAVALUE", ""))
        settlement = parse_nem_time(row.get("SETTLEMENTDATE", ""))
        if mw is not None and settlement is not None:
            rows.append((row["DUID"].strip().upper(), settlement, mw))
    return rows


def dispatch_rows(path: str | Path, duids: Iterable[str]) -> list[tuple]:
    """One row per (DUID, interval): the highest INTERVENTION run when there are two."""
    best: dict[tuple, tuple] = {}
    for _, row in iter_table_rows(path, [UNIT_SOLUTION], duids):
        settlement = parse_nem_time(row.get("SETTLEMENTDATE", ""))
        if settlement is None:
            continue
        duid = row["DUID"].strip().upper()
        intervention = parse_int(row.get("INTERVENTION", "")) or 0
        key = (duid, settlement)
        if key in best and best[key][2] >= intervention:
            continue
        best[key] = (
            duid,
            settlement,
            intervention,
            parse_float(row.get("INITIALMW", "")),
            parse_float(row.get("TOTALCLEARED", "")),
            parse_float(row.get("AVAILABILITY", "")),
            parse_float(row.get("UIGF", "")),
            parse_int(row.get("SEMIDISPATCHCAP", "")),
        )
    return list(best.values())


@dataclass
class _OriginStats:
    submissions: int = 0
    suppressed_aemo: int = 0
    suppressed_participant: int = 0
    max_priority: int = -10**9
    latest_offer: datetime | None = None
    latest_poe50: float | None = None


def intermittent_rows(path: str | Path, duids: Iterable[str]) -> tuple[list[tuple], list[tuple]]:
    """Return (forecast_used rows, forecast_origin_interval rows) for one trading day."""
    stats: dict[tuple, _OriginStats] = defaultdict(_OriginStats)
    poe50: dict[tuple, float | None] = {}
    used: list[tuple] = []
    for table, row in iter_table_rows(path, [DS_RUN, DS_PRED, FORECAST_TRK], duids):
        duid = row["DUID"].strip().upper()
        origin = row.get("ORIGIN", "").strip()
        priority = parse_int(row.get("FORECAST_PRIORITY", ""))
        offer = parse_nem_time(row.get("OFFERDATETIME", ""))
        if table == FORECAST_TRK:
            settlement = parse_nem_time(row.get("SETTLEMENTDATE", ""))
            if settlement is not None:
                used.append((duid, settlement, origin, priority if priority is not None else 0, offer))
            continue
        run = parse_nem_time(row.get("RUN_DATETIME", ""))
        if run is None:
            continue
        if table == DS_RUN:
            item = stats[(duid, run, origin)]
            item.submissions += 1
            item.suppressed_aemo += row.get("SUPPRESSED_AEMO", "").strip() == "1"
            item.suppressed_participant += row.get("SUPPRESSED_PARTICIPANT", "").strip() == "1"
            if priority is not None:
                item.max_priority = max(item.max_priority, priority)
        elif parse_nem_time(row.get("INTERVAL_DATETIME", "")) == run:
            value = parse_float(row.get("FORECAST_POE50", ""))
            poe50[(duid, run, origin, priority, offer)] = value
            item = stats[(duid, run, origin)]
            if offer is not None and (item.latest_offer is None or offer >= item.latest_offer):
                item.latest_offer = offer
                item.latest_poe50 = value

    used_rows = [
        (duid, settlement, origin, priority, offer, poe50.get((duid, settlement, origin, priority, offer)))
        for duid, settlement, origin, priority, offer in used
    ]
    origin_rows = [
        (
            duid,
            run,
            origin,
            item.submissions,
            item.suppressed_aemo,
            item.suppressed_participant,
            item.max_priority if item.submissions else 0,
            item.latest_poe50,
        )
        for (duid, run, origin), item in stats.items()
        if item.submissions  # PRED rows without a RUN row carry no submission to count
    ]
    return used_rows, origin_rows


# ---------------------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------------------

UPSERTS = {
    "scada": (
        "INSERT INTO scada_reading (duid, settlement_date, mw) VALUES (%s, %s, %s) "
        "ON CONFLICT (duid, settlement_date) DO UPDATE SET mw = EXCLUDED.mw"
    ),
    "dispatch": (
        "INSERT INTO dispatch_solution (duid, settlement_date, intervention, initial_mw, total_cleared, "
        "availability, uigf, semidispatch_cap) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (duid, settlement_date) DO UPDATE SET intervention = EXCLUDED.intervention, "
        "initial_mw = EXCLUDED.initial_mw, total_cleared = EXCLUDED.total_cleared, "
        "availability = EXCLUDED.availability, uigf = EXCLUDED.uigf, semidispatch_cap = EXCLUDED.semidispatch_cap"
    ),
    "forecast_used": (
        "INSERT INTO forecast_used (duid, settlement_date, origin, forecast_priority, offer_datetime, poe50) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (duid, settlement_date) DO UPDATE SET "
        "origin = EXCLUDED.origin, forecast_priority = EXCLUDED.forecast_priority, "
        "offer_datetime = EXCLUDED.offer_datetime, poe50 = EXCLUDED.poe50"
    ),
    "forecast_origin": (
        "INSERT INTO forecast_origin_interval (duid, settlement_date, origin, submissions, suppressed_aemo, "
        "suppressed_participant, max_priority, latest_poe50) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (duid, settlement_date, origin) DO UPDATE SET submissions = EXCLUDED.submissions, "
        "suppressed_aemo = EXCLUDED.suppressed_aemo, suppressed_participant = EXCLUDED.suppressed_participant, "
        "max_priority = EXCLUDED.max_priority, latest_poe50 = EXCLUDED.latest_poe50"
    ),
}


class Store:
    """Thin psycopg wrapper. One connection; each file is committed on its own."""

    def __init__(self, database_url: str) -> None:
        import psycopg  # imported here so parser tests do not need a database driver

        self.conn = psycopg.connect(database_url, autocommit=False)

    def close(self) -> None:
        self.conn.close()

    def registry_duids(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT duid FROM registry_unit ORDER BY duid")
            duids = [row[0] for row in cur.fetchall()]
        self.conn.commit()
        return duids

    def seen_files(self, source: str) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT file_name FROM ingested_file WHERE source = %s", (source,))
            names = {row[0] for row in cur.fetchall()}
        self.conn.commit()
        return names

    def start_run(self, source: str, trigger: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingest_run (source, trigger, status) VALUES (%s, %s, 'running') RETURNING id",
                (source, trigger),
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    def finish_run(self, run_id: int, result: "SourceResult") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE ingest_run SET status = %s, finished_at = now(), files_processed = %s, rows_written = %s, "
                "bytes_downloaded = %s, latest_interval = %s, error = %s WHERE id = %s",
                (
                    result.status,
                    result.files,
                    result.rows,
                    result.bytes,
                    result.latest_interval,
                    "\n".join(result.errors)[:4000] or None,
                    run_id,
                ),
            )
        self.conn.commit()

    def load_file(self, source: str, file_name: str, run_id: int, batches: dict[str, list[tuple]]) -> int:
        """Upsert every batch and mark the file loaded, all in one transaction."""
        written = 0
        try:
            with self.conn.cursor() as cur:
                for kind, rows in batches.items():
                    if rows:
                        cur.executemany(UPSERTS[kind], rows)
                        written += len(rows)
                cur.execute(
                    "INSERT INTO ingested_file (source, file_name, run_id, rows) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (source, file_name) DO UPDATE SET run_id = EXCLUDED.run_id, rows = EXCLUDED.rows, "
                    "ingested_at = now()",
                    (source, file_name, run_id, written),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return written

    def has_snapshot(self, duid: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM forecast_snapshot WHERE duid = %s LIMIT 1", (duid,))
            found = cur.fetchone() is not None
        self.conn.commit()
        return found

    def save_snapshot(self, snapshot: dict) -> None:
        from psycopg.types.json import Jsonb

        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_snapshot (duid, as_of, generated_at, horizon_hours, probability, risk_level, "
                "data_lag_hours, model_file, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (duid, as_of) DO UPDATE SET generated_at = EXCLUDED.generated_at, "
                "probability = EXCLUDED.probability, risk_level = EXCLUDED.risk_level, "
                "data_lag_hours = EXCLUDED.data_lag_hours, model_file = EXCLUDED.model_file, payload = EXCLUDED.payload",
                (
                    snapshot["duid"],
                    snapshot["as_of"],
                    snapshot["generated_at"],
                    snapshot["horizon_hours"],
                    snapshot["probability"],
                    snapshot["risk_level"],
                    snapshot["data_lag_hours"],
                    snapshot["model_file"],
                    Jsonb(snapshot["payload"]),
                ),
            )
        self.conn.commit()


# ---------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------


@dataclass
class SourceResult:
    source: str
    files: int = 0
    rows: int = 0
    bytes: int = 0
    latest_interval: datetime | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "failed" if self.errors else "success"

    def as_event(self, run_id: int | None, trigger: str, seconds: float) -> dict:
        return {
            "event": "ingest_run",
            "run_id": run_id,
            "source": self.source,
            "trigger": trigger,
            "status": self.status,
            "files": self.files,
            "rows": self.rows,
            "bytes": self.bytes,
            "latest_interval": self.latest_interval.isoformat() if self.latest_interval else None,
            "seconds": round(seconds, 2),
            "error": self.errors[0][:300] if self.errors else None,
        }


@dataclass
class IngestConfig:
    cache_dir: Path = Path("data/cache")
    scada_days: int = 30
    dispatch_days: int = 30
    intermittent_days: int = 7
    trigger: str = "manual"
    model_paths: Sequence[str] = DEFAULT_MODELS
    max_files: int | None = None  # cap per source, for smoke runs


Downloader = Callable[[str, Path], Path]


def _download(url: str, cache_dir: Path) -> Path:
    return with_retries(lambda: download_file(url, cache_dir=cache_dir), f"downloading {url}")


def _latest(rows: Sequence[tuple]) -> datetime | None:
    return max((row[1] for row in rows), default=None)


def _load_files(
    store: Store,
    source: str,
    run_id: int,
    files: Sequence[RemoteFile],
    cache_dir: Path,
    parse: Callable[[Path], dict[str, list[tuple]]],
    download: Downloader,
    result: SourceResult,
) -> None:
    for item in files:
        try:
            path = download(item.url, cache_dir)
            batches = parse(path)
            result.rows += store.load_file(source, item.name, run_id, batches)
            result.files += 1
            result.bytes += path.stat().st_size
            latest = max((_latest(rows) for rows in batches.values() if rows), default=None)
            if latest and (result.latest_interval is None or latest > result.latest_interval):
                result.latest_interval = latest
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the others
            log.exception("Failed to load %s", item.name)
            result.errors.append(f"{item.name}: {type(exc).__name__}: {exc}")


def _now_nem() -> datetime:
    return datetime.now(NEM_TZ)


def _window_start(days: int, now: datetime) -> datetime:
    start = (now - timedelta(days=days)).date()
    return datetime(start.year, start.month, start.day, tzinfo=NEM_TZ)


def ingest_source(
    store: Store,
    source: str,
    duids: Sequence[str],
    config: IngestConfig,
    download: Downloader = _download,
    lister: Callable[[str, str], list[RemoteFile]] = list_remote,
    now: datetime | None = None,
) -> SourceResult:
    """Run one source end to end and record it as an ``ingest_run``."""
    now = now or _now_nem()
    result = SourceResult(source)
    run_id = store.start_run(source, config.trigger)
    started = time.monotonic()
    try:
        seen = store.seen_files(source)
        if source == "scada":
            since = _window_start(config.scada_days, now)
            files = select_scada_files(
                lister(SCADA_ARCHIVE, "PUBLIC_DISPATCHSCADA_"), lister(SCADA_CURRENT, "PUBLIC_DISPATCHSCADA_"), seen, since
            )
            parse = lambda path: {"scada": scada_rows(path, duids)}  # noqa: E731
            cache = config.cache_dir / "scada"
        elif source == "dispatch":
            files = select_files(lister(DISPATCH_CURRENT, "PUBLIC_NEXT_DAY_DISPATCH_"), seen, _window_start(config.dispatch_days, now))
            parse = lambda path: {"dispatch": dispatch_rows(path, duids)}  # noqa: E731
            cache = config.cache_dir  # where forecast_from_bundle looks for Next Day Dispatch files
        elif source == "intermittent":
            files = select_files(
                lister(INTERMITTENT_CURRENT, "PUBLIC_NEXT_DAY_INTERMITTENT_DS_"), seen, _window_start(config.intermittent_days, now)
            )

            def parse(path: Path) -> dict[str, list[tuple]]:
                used, origins = intermittent_rows(path, duids)
                return {"forecast_used": used, "forecast_origin": origins}

            cache = config.cache_dir / "intermittent"
        else:
            raise ValueError(f"Unknown file source {source!r}")
        if config.max_files is not None:
            files = files[: config.max_files]
        log.info("%s: %d new file(s) to load", source, len(files))
        _load_files(store, source, run_id, files, cache, parse, download, result)
    except Exception as exc:  # noqa: BLE001 - recorded on the run, then reported
        log.exception("%s ingest failed", source)
        result.errors.append(f"{type(exc).__name__}: {exc}")
    store.finish_run(run_id, result)
    emit_event(result.as_event(run_id, config.trigger, time.monotonic() - started))
    return result


def _json_safe(value):
    """NaN/inf are not valid JSON (Postgres jsonb rejects them); numpy scalars become floats."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item") and callable(value.item):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def build_snapshot(payload: dict, model_file: str) -> dict:
    """Shape a ``forecast_from_bundle`` response into a ``forecast_snapshot`` row."""
    as_of = datetime.fromisoformat(payload["as_of"]).replace(tzinfo=NEM_TZ)
    generated = datetime.fromisoformat(payload["generated_at"])
    if generated.tzinfo is None:
        generated = generated.astimezone()
    risk = payload["constraint_risk"]
    return {
        "duid": payload["duid"],
        "as_of": as_of,
        "generated_at": generated,
        "horizon_hours": int(payload["horizon_hours"]),
        "probability": float(risk["probability"]),
        "risk_level": risk["risk_level"],
        "data_lag_hours": payload.get("data_lag_hours"),
        "model_file": model_file,
        "payload": _json_safe(payload),
    }


def ingest_forecasts(
    store: Store,
    duids: Sequence[str],
    config: IngestConfig,
    dispatch_loaded: bool,
    forecaster: Callable[..., dict] | None = None,
) -> SourceResult:
    """Score each bundle whose DUID is a console site, when new dispatch data arrived."""
    import joblib

    if forecaster is None:
        from .forecast import forecast_from_bundle as forecaster

    result = SourceResult("forecast")
    run_id = store.start_run("forecast", config.trigger)
    started = time.monotonic()
    for model_path in config.model_paths:
        try:
            bundle_duid = str(joblib.load(model_path)["duid"]).upper()
            if bundle_duid not in duids:
                continue
            if not dispatch_loaded and store.has_snapshot(bundle_duid):
                log.info("forecast: no new dispatch data for %s; keeping the current snapshot", bundle_duid)
                continue
            # "auto": a fresh CI runner may hold fewer cached Next Day Dispatch days than the
            # model's history window, so missing days are downloaded.
            payload = forecaster(model_path, duid=bundle_duid, source="auto", cache_dir=config.cache_dir)
            snapshot = build_snapshot(payload, Path(model_path).name)
            store.save_snapshot(snapshot)
            result.files += 1
            result.rows += 1
            result.latest_interval = snapshot["as_of"]
        except Exception as exc:  # noqa: BLE001 - one bundle must not stop the others
            log.exception("forecast failed for %s", model_path)
            result.errors.append(f"{Path(model_path).name}: {type(exc).__name__}: {exc}")
    store.finish_run(run_id, result)
    emit_event(result.as_event(run_id, config.trigger, time.monotonic() - started))
    return result


def emit_event(event: dict) -> None:
    """One structured log line per source run, also sent to App Insights when configured."""
    log.info("ingest_run %s", json.dumps(event, default=str))
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if connection_string:
        try:
            send_custom_event(connection_string, "ingest_run", event)
        except Exception as exc:  # noqa: BLE001 - telemetry must never fail the ingest
            log.warning("Could not send ingest_run telemetry: %s", exc)


NUMERIC_EVENT_FIELDS = ("files", "rows", "bytes", "seconds")


def build_event_envelope(connection_string: str, name: str, event: dict, now: datetime | None = None) -> tuple[str, list]:
    """App Insights track-API URL and payload for one custom event.

    String fields become ``customDimensions``; counts become ``customMeasurements``. The
    ingestion alert queries ``customEvents | where name == "ingest_run"``.
    """
    parts = dict(item.split("=", 1) for item in connection_string.split(";") if "=" in item)
    ikey = parts.get("InstrumentationKey")
    endpoint = parts.get("IngestionEndpoint", "https://dc.services.visualstudio.com/").rstrip("/")
    if not ikey:
        raise ValueError("APPLICATIONINSIGHTS_CONNECTION_STRING has no InstrumentationKey.")
    stamp = (now or datetime.now().astimezone()).isoformat()
    properties = {k: str(v) for k, v in event.items() if v is not None and k not in NUMERIC_EVENT_FIELDS}
    measurements = {k: float(event[k]) for k in NUMERIC_EVENT_FIELDS if event.get(k) is not None}
    envelope = {
        "name": "Microsoft.ApplicationInsights.Event",
        "time": stamp,
        "iKey": ikey,
        "tags": {"ai.cloud.role": "ops-ingest"},
        "data": {
            "baseType": "EventData",
            "baseData": {"ver": 2, "name": name, "properties": properties, "measurements": measurements},
        },
    }
    return f"{endpoint}/v2/track", [envelope]


def send_custom_event(connection_string: str, name: str, event: dict) -> None:
    import requests

    url, payload = build_event_envelope(connection_string, name, event)
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()


def run(database_url: str, sources: Sequence[str], config: IngestConfig) -> list[SourceResult]:
    store = Store(database_url)
    try:
        duids = store.registry_duids()
        if not duids:
            raise RuntimeError("registry_unit is empty: run the console seed (npm run db:seed) first.")
        results = []
        for source in sources:
            if source == "forecast":
                dispatch_loaded = any(r.source == "dispatch" and r.files > 0 for r in results)
                results.append(ingest_forecasts(store, duids, config, dispatch_loaded))
            else:
                results.append(ingest_source(store, source, duids, config))
        return results
    finally:
        store.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load NEMWeb operational data for the ops console.")
    parser.add_argument("--sources", default=",".join(SOURCES), help=f"Comma-separated subset of {SOURCES}.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--scada-days", type=int, default=30)
    parser.add_argument("--dispatch-days", type=int, default=30)
    parser.add_argument("--intermittent-days", type=int, default=7)
    parser.add_argument("--trigger", default="manual", choices=["manual", "schedule", "backfill"])
    parser.add_argument("--model", action="append", dest="models", help="Model bundle path (repeatable).")
    parser.add_argument("--max-files", type=int, default=None, help="Cap files per source (smoke runs).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_arg_parser().parse_args(argv)
    if not args.database_url:
        print("DATABASE_URL is not set (use --database-url or the environment).", file=sys.stderr)
        return 2
    sources = [item.strip() for item in args.sources.split(",") if item.strip()]
    unknown = sorted(set(sources) - set(SOURCES))
    if unknown:
        print(f"Unknown source(s): {unknown}. Choose from {SOURCES}.", file=sys.stderr)
        return 2
    config = IngestConfig(
        cache_dir=Path(args.cache_dir),
        scada_days=args.scada_days,
        dispatch_days=args.dispatch_days,
        intermittent_days=args.intermittent_days,
        trigger=args.trigger,
        model_paths=args.models or DEFAULT_MODELS,
        max_files=args.max_files,
    )
    results = run(args.database_url, sources, config)
    failed = [result.source for result in results if result.status == "failed"]
    if failed:
        print(f"Ingest finished with failures in: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
