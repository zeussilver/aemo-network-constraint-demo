#!/usr/bin/env python
"""Data checks on the ops console database, written to a JSON file the docs quote.

- Volume: rows, DUIDs and NEM days covered per ops table.
- Cross-check (target T3): DISPATCH_UNIT_SCADA ``SCADAVALUE`` and Next Day Dispatch
  ``INITIALMW`` both describe MW at the start of the same dispatch interval, so they
  should agree; the median absolute difference is the check.
- Ingest runs: files, rows, bytes and duration per source.

    DATABASE_URL=... python scripts/ops_data_checks.py --out artifacts/ops_data_checks.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg

QUERIES = {
    "volume": """
        SELECT 'scada_reading' AS tbl, count(*), count(DISTINCT duid), min(settlement_date), max(settlement_date) FROM scada_reading
        UNION ALL SELECT 'dispatch_solution', count(*), count(DISTINCT duid), min(settlement_date), max(settlement_date) FROM dispatch_solution
        UNION ALL SELECT 'forecast_used', count(*), count(DISTINCT duid), min(settlement_date), max(settlement_date) FROM forecast_used
        UNION ALL SELECT 'forecast_origin_interval', count(*), count(DISTINCT duid), min(settlement_date), max(settlement_date) FROM forecast_origin_interval
        UNION ALL SELECT 'forecast_snapshot', count(*), count(DISTINCT duid), min(as_of), max(as_of) FROM forecast_snapshot
    """,
    "scada_vs_initialmw": """
        SELECT count(*) AS pairs,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(s.mw - d.initial_mw)) AS median_abs_diff_mw,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY abs(s.mw - d.initial_mw)) AS p95_abs_diff_mw,
               avg((abs(s.mw - d.initial_mw) <= 1.0)::int) AS share_within_1mw
        FROM scada_reading s JOIN dispatch_solution d USING (duid, settlement_date)
        WHERE d.initial_mw IS NOT NULL
    """,
    "ingest_runs": """
        SELECT source, trigger, status, files_processed, rows_written, bytes_downloaded,
               extract(epoch FROM finished_at - started_at) AS seconds
        FROM ingest_run ORDER BY id
    """,
}


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__float__") and not isinstance(value, (int, bool)):
        return float(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--out", default="artifacts/ops_data_checks.json")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("DATABASE_URL is not set.")

    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with psycopg.connect(args.database_url) as conn, conn.cursor() as cur:
        cur.execute(QUERIES["volume"])
        report["volume"] = [
            {"table": t, "rows": n, "duids": d, "first": _jsonable(a), "last": _jsonable(b)} for t, n, d, a, b in cur.fetchall()
        ]
        cur.execute(QUERIES["scada_vs_initialmw"])
        columns = [c.name for c in cur.description]
        report["scada_vs_initialmw"] = {k: _jsonable(v) for k, v in zip(columns, cur.fetchone())}
        cur.execute(QUERIES["ingest_runs"])
        columns = [c.name for c in cur.description]
        report["ingest_runs"] = [{k: _jsonable(v) for k, v in zip(columns, row)} for row in cur.fetchall()]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
