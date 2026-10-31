"""Load a simulated world into a sandbox copy of the ops console database, and keep it live.

- ``load`` empties the sandbox (staff accounts are kept), creates the world's customers,
  sites and simulated DUIDs (with audit rows, as if staff had created them), writes every
  ops row AEMO would have published by the anchor time, the ingest runs a scheduled job
  would have left, and a ``sim_run`` row holding the scenario manifest and training answers.
- ``tick`` plays the ingest job: it extends the same world up to now and writes the rows
  published since the last tick. Generation is prefix-stable, so earlier rows never change.
- ``preview`` prints the manifest for an anchor without touching a database.

The ops rows go through the same upserts as the real ingest job (``ops_ingest.UPSERTS``), so
the console reads simulated and real data through one code path.

A safety latch refuses any database whose name does not end in ``_sandbox`` or ``_e2e``:
``load`` empties tables, and simulated rows must never reach the production database.

    python -m src.aemo_constraint_demo.sim_sandbox load --world training --database-url .../ops_console_sandbox
    python -m src.aemo_constraint_demo.sim_sandbox tick --database-url .../ops_console_sandbox
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import date, datetime
from typing import Sequence
from urllib.parse import urlsplit

from .nemweb_stream import NEM_TZ
from .ops_ingest import UPSERTS
from .sim_engine import Calibration, SiteSpec, nem
from .sim_scenarios import Anchored, World, anchor_world, generate, ingest_runs, load_world, manifest, published_rows

log = logging.getLogger("sim_sandbox")

SANDBOX_SUFFIXES = ("_sandbox", "_e2e")
SIMULATOR_ACTOR = ("simulator", "sandbox-simulator")
OPS_TABLES = {"scada": "scada_reading", "dispatch": "dispatch_solution", "forecast_used": "forecast_used", "forecast_origin": "forecast_origin_interval"}
RESET_TABLES = (
    "sim_run", "audit_log", "unit", "site", "customer", "registry_unit", "scada_reading", "dispatch_solution",
    "forecast_used", "forecast_origin_interval", "forecast_snapshot", "ingested_file", "ingest_run",
)
TABLE_SOURCE = {"scada": "scada", "dispatch": "dispatch", "forecast_used": "intermittent", "forecast_origin": "intermittent"}


def assert_sandbox_database(database_url: str) -> str:
    name = urlsplit(database_url).path.lstrip("/")
    if not name.endswith(SANDBOX_SUFFIXES):
        raise ValueError(f'Refusing to write simulated data to "{name}": a sandbox database name must end in _sandbox or _e2e.')
    return name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class SandboxStore:
    def __init__(self, database_url: str, password: str | None = None) -> None:
        import psycopg

        assert_sandbox_database(database_url)
        extra = {"password": password} if password else {}
        self.conn = psycopg.connect(database_url, autocommit=False, **extra)

    def close(self) -> None:
        self.conn.close()

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {', '.join(RESET_TABLES)} RESTART IDENTITY CASCADE")

    def write_reference(self, world: World, now: datetime) -> dict[str, int]:
        """Customers, sites, simulated registry units and the audit rows staff would leave."""
        customers = {}
        audit: list[tuple] = []
        with self.conn.cursor() as cur:
            for name in dict.fromkeys(site.customer for site in world.sites):
                customer_id = f"sim-{_slug(name)}"
                record = {"name": name, "contactEmail": f"ops-{_slug(name)}@example.com", "status": "ACTIVE", "isSynthetic": True}
                cur.execute(
                    "INSERT INTO customer (id, name, contact_email, status, notes, is_synthetic, version, created_at, updated_at) "
                    'VALUES (%s, %s, %s, %s::"CustomerStatus", %s, true, 1, %s, %s)',
                    (customer_id, name, record["contactEmail"], "ACTIVE", "Simulated customer created by the sandbox simulator. Not a real organisation.", now, now),
                )
                customers[name] = customer_id
                audit.append(("create", "customer", customer_id, record))
            for index, site in enumerate(world.sites, start=1):
                self._write_site(cur, site, customers[site.customer], index, world.name, now)
                audit.append(("create", "site", f"sim-{site.key}", _site_record(site)))
                audit.append(("assign_unit", "unit", site.duid, {"duid": site.duid, "siteId": f"sim-{site.key}"}))
            cur.executemany(
                "INSERT INTO audit_log (id, actor_id, actor_email, action, entity_type, entity_id, before, after, at) "
                "VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s)",
                [(uuid.uuid4().hex, *SIMULATOR_ACTOR, action, entity, entity_id, json.dumps(after), now) for action, entity, entity_id, after in audit],
            )
        return {"customers": len(customers), "sites": len(world.sites), "audit_rows": len(audit)}

    @staticmethod
    def _write_site(cur, site: SiteSpec, customer_id: str, index: int, world: str, now: datetime) -> None:
        technology = "Simulated solar PV" if site.fuel == "SOLAR" else "Simulated onshore wind"
        cur.execute(
            "INSERT INTO registry_unit (duid, station_name, station_id, region_id, fuel, technology, classification, "
            "schedule_type, dispatch_type, connection_point_id, registered_capacity_mw, max_capacity_mw, "
            "registration_list_file, standing_data_file) "
            'VALUES (%s, %s, %s, %s::"Region", %s::"Fuel", %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (
                site.duid, site.name, site.duid, site.region, site.fuel, technology, "Semi-Scheduled", "SEMI-SCHEDULED",
                "GENERATOR", f"SIMCP{index}", site.capacity_mw, site.capacity_mw, f"simulator: data/sim/{world}.toml", "simulator",
            ),
        )
        cur.execute(
            "INSERT INTO site (id, customer_id, name, region, fuel, registered_capacity_mw, status, version, created_at, updated_at) "
            'VALUES (%s, %s, %s, %s::"Region", %s::"Fuel", %s, %s::"SiteStatus", 1, %s, %s)',
            (f"sim-{site.key}", customer_id, site.name, site.region, site.fuel, site.capacity_mw, "OPERATING", now, now),
        )
        cur.execute("INSERT INTO unit (duid, site_id, created_at) VALUES (%s, %s, %s)", (site.duid, f"sim-{site.key}", now))

    def write_rows(self, rows: dict[str, list[tuple]]) -> dict[str, int]:
        with self.conn.cursor() as cur:
            for kind, batch in rows.items():
                if batch:
                    cur.executemany(UPSERTS[kind], batch)
        return {OPS_TABLES[kind]: len(batch) for kind, batch in rows.items()}

    def write_runs(self, runs: Sequence[dict]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ingest_run (source, trigger, status, started_at, finished_at, files_processed, rows_written, "
                "bytes_downloaded, latest_interval, error) VALUES (%(source)s, %(trigger)s, %(status)s, %(started_at)s, "
                "%(finished_at)s, %(files_processed)s, %(rows_written)s, 0, %(latest_interval)s, %(error)s)",
                runs,
            )

    def write_sim_run(self, anchored: Anchored, document: dict) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sim_run (world, seed, anchor, reference_day, manifest) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (anchored.world.name, anchored.world.seed, anchored.anchor, anchored.reference_day, json.dumps(document)),
            )
            return cur.fetchone()[0]

    def latest_sim_run(self) -> tuple[str, int, datetime] | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT world, seed, anchor FROM sim_run ORDER BY id DESC LIMIT 1")
            return cur.fetchone()

    def newest_by_duid(self) -> dict[str, dict[str, datetime]]:
        newest: dict[str, dict[str, datetime]] = {}
        with self.conn.cursor() as cur:
            for kind, table in OPS_TABLES.items():
                cur.execute(f"SELECT duid, max(settlement_date) FROM {table} GROUP BY duid")
                newest[kind] = {duid: nem(latest) for duid, latest in cur.fetchall()}
        return newest

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()


def _site_record(site: SiteSpec) -> dict:
    return {"name": site.name, "region": site.region, "fuel": site.fuel, "registeredCapacityMw": site.capacity_mw, "status": "OPERATING"}


def load(
    database_url: str,
    world: World,
    anchor: datetime,
    calibration: Calibration | None = None,
    password: str | None = None,
) -> dict:
    """Replace the sandbox's contents with ``world`` as it stands at ``anchor``."""
    calibration = calibration or Calibration.load()
    anchored = anchor_world(world, anchor, calibration)
    intervals = generate(anchored, calibration)
    rows = published_rows(anchored, intervals, calibration, anchored.anchor)
    runs = ingest_runs(anchored, rows, calibration, anchored.anchor)
    document = manifest(anchored, rows, runs)
    store = SandboxStore(database_url, password)
    try:
        store.reset()
        reference = store.write_reference(world, anchored.anchor)
        written = store.write_rows(rows)
        store.write_runs(runs)
        run_id = store.write_sim_run(anchored, document)
        store.commit()
    except Exception:
        store.rollback()
        raise
    finally:
        store.close()
    return {
        "sim_run": run_id,
        "world": world.name,
        "anchor": anchored.anchor.isoformat(),
        "reference_day": anchored.reference_day.isoformat(),
        **reference,
        "rows": written,
        "ingest_runs": len(runs),
        "tasks": [task["id"] for task in document["tasks"]],
    }


def tick(database_url: str, at: datetime, calibration: Calibration | None = None, password: str | None = None) -> dict:
    """Publish what the loaded world has produced since the last tick, as one ingest run per source."""
    calibration = calibration or Calibration.load()
    at = nem(at)
    store = SandboxStore(database_url, password)
    try:
        latest = store.latest_sim_run()
        if latest is None:
            raise RuntimeError("The sandbox has no sim_run: load a world first.")
        world_name, seed, anchor = latest
        world = load_world(world_name)
        if world.seed != seed:
            raise RuntimeError(f"data/sim/{world_name}.toml changed seed since the sandbox was loaded; load it again.")
        anchored = anchor_world(world, nem(anchor), calibration)
        rows = published_rows(anchored, generate(anchored, calibration, until=at), calibration, at)
        newest = store.newest_by_duid()
        fresh = {kind: [row for row in batch if row[1] > newest[kind].get(row[0], anchored.start)] for kind, batch in rows.items()}
        written = store.write_rows(fresh)
        runs = []
        for source in ("scada", "dispatch", "intermittent"):
            batch = [row for kind, rows_ in fresh.items() if TABLE_SOURCE[kind] == source for row in rows_]
            if batch:
                runs.append(
                    {
                        "source": source, "trigger": "simulator", "status": "success", "started_at": at, "finished_at": at,
                        "files_processed": 1, "rows_written": len(batch), "latest_interval": max(row[1] for row in batch), "error": None,
                    }
                )
        store.write_runs(runs)
        store.commit()
    except Exception:
        store.rollback()
        raise
    finally:
        store.close()
    return {"at": at.isoformat(), "rows": written, "ingest_runs": [run["source"] for run in runs]}


def _parse_time(value: str) -> datetime:
    if value == "now":
        return datetime.now(NEM_TZ)
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=NEM_TZ)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulated sandbox for the ops console.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("load", "Empty the sandbox and load a world at an anchor time."),
        ("tick", "Publish what the loaded world produced since the last tick."),
        ("run", "Tick on a fixed period (the live sandbox feed)."),
        ("preview", "Print the manifest for a world and anchor; no database."),
    ):
        command = commands.add_parser(name, help=help_text)
        if name != "preview":
            command.add_argument("--database-url", default=os.environ.get("SANDBOX_DATABASE_URL"))
        if name in ("load", "preview"):
            command.add_argument("--world", default="training", help="World name in data/sim/ or a path to a .toml file.")
            command.add_argument("--anchor", default="now", help='ISO time (NEM time if no offset) or "now".')
        if name == "tick":
            command.add_argument("--at", default="now")
        if name == "run":
            command.add_argument("--every-seconds", type=int, default=3600, help="Default: hourly, like the ingest schedule.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_arg_parser().parse_args(argv)
    if args.command == "preview":
        calibration = Calibration.load()
        anchored = anchor_world(load_world(args.world), _parse_time(args.anchor), calibration)
        rows = published_rows(anchored, generate(anchored, calibration), calibration, anchored.anchor)
        print(json.dumps(manifest(anchored, rows, ingest_runs(anchored, rows, calibration, anchored.anchor)), indent=2, default=_json_default))
        return 0
    if not args.database_url:
        print("Set SANDBOX_DATABASE_URL or pass --database-url.", file=sys.stderr)
        return 2
    try:
        assert_sandbox_database(args.database_url)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.command == "load":
        print(json.dumps(load(args.database_url, load_world(args.world), _parse_time(args.anchor)), indent=2))
    elif args.command == "tick":
        print(json.dumps(tick(args.database_url, _parse_time(args.at)), indent=2))
    else:
        while True:
            log.info("tick %s", json.dumps(tick(args.database_url, datetime.now(NEM_TZ))))
            time.sleep(args.every_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
