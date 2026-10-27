#!/usr/bin/env python
"""Recalibrate the quantile model's intervals and score what that costs, written to JSON the docs quote.

The backtest leaves one clear failure behind: the model is the sharpest forecast in the lane
and its 80% interval holds the settled price 65% of the time. This script asks whether a
rolling conformal correction — offsets read from residuals that had already settled when the
row was published — fixes the calibration, and what it charges in CRPS for doing so.

Like the ensemble script it reads the scored rows `price_backtest.py` saved, so no model is
refitted. The trailing window is the one free parameter, so every candidate window is scored
and the table is reported rather than a single chosen number.

    python scripts/price_conformal_backtest.py --region NSW1
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.price_backtest import read_predictions
from scripts.price_ensemble_backtest import SLICES, score_slices
from src.aemo_constraint_demo import price_conformal as conformal
from src.aemo_constraint_demo import price_metrics as metrics

log = logging.getLogger("price_conformal_backtest")


def calibration_row(actual: np.ndarray, predictions: dict[float, np.ndarray]) -> dict:
    """The numbers the window choice turns on: sharpness, and every interval's honesty."""
    intervals = {row["nominal"]: row for row in metrics.central_intervals(actual, predictions)}
    point = metrics.point_metrics(actual, predictions[0.5])
    return {
        "crps": round(metrics.crps_from_quantiles(actual, predictions), 4),
        "mae_median": point["mae"],
        "median_abs_error": point["median_abs_error"],
        "coverage_50": intervals.get(0.5, {}).get("coverage"),
        "coverage_80": intervals.get(0.8, {}).get("coverage"),
        "coverage_90": intervals.get(0.9, {}).get("coverage"),
        "width_80": intervals.get(0.8, {}).get("mean_width"),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="NSW1")
    parser.add_argument("--price-dir", default="data/price")
    parser.add_argument("--predictions", default=None)
    parser.add_argument("--forecast", default="model", help="The forecast to recalibrate.")
    parser.add_argument(
        "--windows",
        default="7,14,28,56",
        help="Trailing windows in settled days to compare, as the docs report them.",
    )
    parser.add_argument("--window", type=int, default=conformal.DEFAULT_WINDOW_DAYS)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    region = args.region.upper()
    path = Path(args.predictions) if args.predictions else Path(args.price_dir) / f"backtest_predictions_{region}.csv.gz"
    scored = pd.read_csv(path, parse_dates=["published", "target"]).sort_values("target").reset_index(drop=True)
    log.info("%s: %d scored rows from %s", region, len(scored), path)

    actual = scored["actual_rrp"].to_numpy(dtype=float)
    base = read_predictions(scored, args.forecast)
    baseline = calibration_row(actual, base)

    windows = [int(value) for value in args.windows.split(",") if value.strip()]
    if args.window not in windows:
        windows.append(args.window)
    sweep = {}
    chosen = None
    for window in sorted(windows):
        result = conformal.rolling_conformal(scored, base, window_days=window)
        sweep[str(window)] = calibration_row(actual, result.predictions)
        sweep[str(window)]["crps_vs_baseline_pct"] = round(
            100 * (sweep[str(window)]["crps"] - baseline["crps"]) / baseline["crps"], 2
        )
        if window == args.window:
            chosen = result
        log.info(
            "%-3d day window  CRPS %8.3f (%+5.2f%%)  coverage 50/80/90 %.3f %.3f %.3f",
            window, sweep[str(window)]["crps"], sweep[str(window)]["crps_vs_baseline_pct"],
            sweep[str(window)]["coverage_50"], sweep[str(window)]["coverage_80"],
            sweep[str(window)]["coverage_90"],
        )
    assert chosen is not None  # the chosen window is always in the sweep

    calibrated_name = f"{args.forecast}_conformal"
    predictions = {args.forecast: base, calibrated_name: chosen.predictions}
    scores = score_slices(scored, predictions, slices=SLICES + (("fold", "by_fold"),))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": region,
        "predictions": str(path),
        "rows": int(len(scored)),
        "forecast": args.forecast,
        "window_days": args.window,
        "baseline": baseline,
        "windows": sweep,
        "offsets": conformal.offset_summary(chosen),
        "reliability": {
            name: metrics.reliability_table(actual, quantiles) for name, quantiles in predictions.items()
        },
        "intervals": {
            name: metrics.central_intervals(actual, quantiles) for name, quantiles in predictions.items()
        },
        **scores,
    }

    out = Path(args.out) if args.out else Path(f"artifacts/price_conformal_{region}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    log.info("wrote %s", out)
    log.info(
        "%s: %d of %d rows corrected from a %d-day window",
        region, report["offsets"]["rows_corrected"], report["rows"], args.window,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
