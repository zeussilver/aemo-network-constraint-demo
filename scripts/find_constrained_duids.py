#!/usr/bin/env python
"""Scan candidate DUIDs for historical constraint-binding labels."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.aemo_constraint_demo.scan import (
    SCAN_OUTPUT_COLUMNS,
    STATUS_DOWNLOAD_FAILED,
    STATUS_PARSE_FAILED,
    ScanThresholds,
    discover_candidate_duids,
    failure_result,
    filter_binding_dispatch_constraints,
    filter_dispatch_table_to_recent_days,
    filter_dispatchload_to_duids,
    load_dispatch_tables_for_window,
    load_standing_data,
    parse_duid_list,
    rank_scan_results,
    read_duid_file,
    split_dispatchload_by_duid,
    summarise_duid_window_horizons,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find DUID/windows with real constraint-binding labels.")
    parser.add_argument("--days", nargs="+", type=int, required=True, help="One or more recent rolling windows.")
    parser.add_argument("--out", default="artifacts/duid_constraint_scan.csv", help="Output CSV path.")
    parser.add_argument("--top-out", default=None, help="Optional ranked output CSV path.")
    parser.add_argument("--duids", default=None, help="Comma-separated DUID list.")
    parser.add_argument("--duids-file", default=None, help="File containing one DUID per line.")
    parser.add_argument("--max-duids", type=int, default=None, help="Optional cap for candidate DUIDs.")
    parser.add_argument(
        "--horizons-hours",
        nargs="+",
        type=int,
        default=[0],
        help="Forecast horizons in hours. Default 0 keeps the current-interval Phase 1 label.",
    )
    parser.add_argument("--min-positive-samples", type=int, default=20)
    parser.add_argument("--min-positive-rate", type=float, default=0.005)
    parser.add_argument("--max-positive-rate", type=float, default=0.10)
    parser.add_argument("--max-positive-rate-horizon", type=float, default=0.25)
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--static-year", type=int, default=None, help="Optional MMSDM standing-data year.")
    parser.add_argument("--static-month", type=int, default=None, help="Optional MMSDM standing-data month.")
    parser.add_argument("--end-date", default=None, help="Optional latest Next Day Dispatch trading date.")
    parser.add_argument("--bind-threshold", type=float, default=0.0)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    thresholds = ScanThresholds(
        min_positive_samples=args.min_positive_samples,
        min_positive_rate=args.min_positive_rate,
        max_positive_rate=args.max_positive_rate,
    )
    horizon_thresholds = ScanThresholds(
        min_positive_samples=args.min_positive_samples,
        min_positive_rate=args.min_positive_rate,
        max_positive_rate=args.max_positive_rate_horizon,
    )
    horizons = _normalise_cli_horizons(args.horizons_hours)

    try:
        standing = load_standing_data(
            static_year=args.static_year,
            static_month=args.static_month,
            cache_dir=args.cache_dir,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should report NEMWeb/cache failures clearly
        print(f"Failed to load standing data: {exc}", file=sys.stderr)
        return 1

    duids = _resolve_candidates(args, standing["DUDETAIL"])
    if not duids:
        print("No candidate DUIDs found.", file=sys.stderr)
        return 1

    rows: list[dict] = []
    max_days = max(args.days)
    if args.verbose:
        print(f"Loading dispatch window: days={max_days}", flush=True)
    try:
        dispatch_source = load_dispatch_tables_for_window(
            days=max_days,
            cache_dir=args.cache_dir,
            end_date=args.end_date,
        )
    except Exception as exc:  # noqa: BLE001 - produce rows for the failed scan
        status = _status_for_dispatch_failure(exc)
        reason = str(exc)
        if args.verbose:
            print(f"Dispatch load failed for days={max_days}: {reason}", file=sys.stderr, flush=True)
        for days in args.days:
            rows.extend(
                failure_result(
                    duid,
                    days,
                    status,
                    reason,
                    dudetail=standing["DUDETAIL"],
                    horizon_hours=horizon,
                )
                for duid in duids
                for horizon in horizons
            )
        return _write_outputs(rows, args)

    candidate_dispatchload = filter_dispatchload_to_duids(dispatch_source["DISPATCHLOAD"], duids)
    binding_dispatchconstraint = filter_binding_dispatch_constraints(
        dispatch_source["DISPATCHCONSTRAINT"],
        bind_threshold=args.bind_threshold,
    )

    for days in args.days:
        if args.verbose:
            print(f"Scanning dispatch window: days={days}", flush=True)

        dispatchload_window = filter_dispatch_table_to_recent_days(candidate_dispatchload, days)
        dispatchconstraint_window = filter_dispatch_table_to_recent_days(binding_dispatchconstraint, days)
        dispatchload_by_duid = split_dispatchload_by_duid(dispatchload_window, duids=duids)
        empty_dispatchload = dispatchload_window.head(0)
        for index, duid in enumerate(duids, start=1):
            duid_rows = summarise_duid_window_horizons(
                duid=duid,
                days=days,
                thresholds=thresholds,
                dudetail=standing["DUDETAIL"],
                spd_connectionpoint=standing["SPDCONNECTIONPOINTCONSTRAINT"],
                dispatchload=dispatchload_by_duid.get(duid.upper(), empty_dispatchload),
                dispatchconstraint=dispatchconstraint_window,
                bind_threshold=args.bind_threshold,
                horizons_hours=horizons,
                horizon_thresholds=horizon_thresholds,
            )
            rows.extend(duid_rows)
            if args.verbose:
                for row in duid_rows:
                    print(
                        f"[{index}/{len(duids)}] days={days} horizon={row['horizon_hours']}h "
                        f"duid={row['duid']} status={row['status']} "
                        f"positives={row['positive_intervals']} rate={row['positive_rate']:.4f} "
                        f"recommended={row['recommended']}",
                        flush=True,
                    )

    return _write_outputs(rows, args)


def _write_outputs(rows: list[dict], args: argparse.Namespace) -> int:
    full = pd.DataFrame(rows, columns=SCAN_OUTPUT_COLUMNS)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    full.to_csv(out_path, index=False)

    top = rank_scan_results(full)
    top_path = Path(args.top_out) if args.top_out else out_path.with_name(f"{out_path.stem}_top{out_path.suffix}")
    top_path.parent.mkdir(parents=True, exist_ok=True)
    top.to_csv(top_path, index=False)

    recommended_count = int(top["recommended"].astype(bool).sum()) if not top.empty else 0
    positive_count = int((top["positive_intervals"].fillna(0).astype(int) > 0).sum()) if not top.empty else 0
    print(f"Wrote {len(full)} scan rows to {out_path}")
    print(f"Wrote ranked scan rows to {top_path}")
    print(f"Rows with positive samples: {positive_count}; recommended rows: {recommended_count}")
    return 0


def _resolve_candidates(args: argparse.Namespace, dudetail: pd.DataFrame) -> list[str]:
    if args.duids:
        candidates = parse_duid_list(args.duids)
    elif args.duids_file:
        candidates = read_duid_file(args.duids_file)
    else:
        candidates = discover_candidate_duids(dudetail, max_duids=args.max_duids)

    if args.max_duids and args.max_duids > 0:
        candidates = candidates[: args.max_duids]
    return candidates


def _status_for_dispatch_failure(exc: Exception) -> str:
    message = str(exc).lower()
    if "parse" in message or "csv" in message or "zip" in message:
        return STATUS_PARSE_FAILED
    return STATUS_DOWNLOAD_FAILED


def _normalise_cli_horizons(values: list[int]) -> list[int]:
    out: list[int] = []
    for value in values:
        horizon = int(value)
        if horizon < 0:
            raise SystemExit("--horizons-hours values must be non-negative")
        if horizon not in out:
            out.append(horizon)
    return out or [0]


if __name__ == "__main__":
    raise SystemExit(main())
