#!/usr/bin/env python
"""Score adaptive combinations of the price forecasts, written to JSON the docs quote.

Reads the scored test rows that `price_backtest.py` saved, so the expensive part — fitting
a quantile model per fold — is not repeated. On those same rows it builds three adaptive
combinations and scores them beside the four forecasts they combine:

- ``ensemble_weight``: inverse-recent-loss weights over all forecasts;
- ``ensemble_weight_regime``: the same, tracked separately per market regime;
- ``ensemble_select``: the recent leader per regime, winner takes all.

The honest comparison is against the best fixed choice, not against the average forecast:
an adaptive rule only earns its complexity if it beats whichever single forecast would have
been picked with hindsight.

    python scripts/price_ensemble_backtest.py --region NSW1
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

from scripts.price_backtest import FORECASTS, compact_score, read_predictions
from src.aemo_constraint_demo import price_ensemble as ens
from src.aemo_constraint_demo import price_metrics as metrics

log = logging.getLogger("price_ensemble_backtest")

COMBINATIONS = {
    "ensemble_weight": {"mode": "weight", "by_regime": False},
    "ensemble_weight_regime": {"mode": "weight", "by_regime": True},
    "ensemble_select": {"mode": "select", "by_regime": True},
}


SLICES = (("horizon_bucket", "by_horizon"), ("regime", "by_regime"))


def score_slices(
    scored: pd.DataFrame,
    predictions: dict[str, dict[float, np.ndarray]],
    slices: tuple[tuple[str, str], ...] = SLICES,
) -> dict:
    """Compact scores for every forecast, overall and for each slice of the test rows."""
    actual = scored["actual_rrp"].to_numpy(dtype=float)
    report = {
        "overall": {name: compact_score(actual, quantiles) for name, quantiles in predictions.items()},
        **{key: {} for _, key in slices},
    }
    for column, key in slices:
        for value, index in scored.groupby(column, sort=False).indices.items():
            mask = np.zeros(len(scored), dtype=bool)
            mask[index] = True
            report[key][str(value)] = {
                name: compact_score(actual[mask], {level: values[mask] for level, values in quantiles.items()})
                for name, quantiles in predictions.items()
            }
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="NSW1")
    parser.add_argument("--price-dir", default="data/price")
    parser.add_argument("--predictions", default=None)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    region = args.region.upper()
    path = Path(args.predictions) if args.predictions else Path(args.price_dir) / f"backtest_predictions_{region}.csv.gz"
    scored = pd.read_csv(path, parse_dates=["published", "target"]).sort_values("target").reset_index(drop=True)
    log.info("%s: %d scored rows from %s", region, len(scored), path)

    # Every combination draws on the four base forecasts only. Letting one ensemble become a
    # member of the next would weight the same forecast twice and flatter the result.
    members: dict[str, dict[float, np.ndarray]] = {
        name: read_predictions(scored, name) for name in FORECASTS
    }
    predictions: dict[str, dict[float, np.ndarray]] = dict(members)
    weights_report = {}
    for name, settings in COMBINATIONS.items():
        result = ens.adaptive_ensemble(scored, members, window_days=args.window_days, **settings)
        predictions[name] = result.predictions
        weights_report[name] = ens.weight_summary(result.weights, labels=scored["regime"])
        weights_report[name]["by_horizon"] = ens.weight_summary(
            result.weights, labels=scored["horizon_bucket"]
        )["by_label"]

    actual = scored["actual_rrp"].to_numpy(dtype=float)
    scores = score_slices(scored, predictions)
    best_fixed = min(FORECASTS, key=lambda name: scores["overall"][name]["crps"])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": region,
        "predictions": str(path),
        "rows": int(len(scored)),
        "window_days": args.window_days,
        "best_fixed_forecast": best_fixed,
        "crps_vs_best_fixed": {
            name: round(
                100 * (scores["overall"][name]["crps"] - scores["overall"][best_fixed]["crps"])
                / scores["overall"][best_fixed]["crps"],
                2,
            )
            for name in predictions
        },
        "weights": weights_report,
        "reliability": {
            name: metrics.reliability_table(actual, quantiles) for name, quantiles in predictions.items()
        },
        **scores,
    }

    out = Path(args.out) if args.out else Path(f"artifacts/price_ensemble_{region}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    log.info("wrote %s", out)
    for name in predictions:
        overall = scores["overall"][name]
        log.info(
            "%-24s CRPS %8.3f (%+6.2f%% vs %s)  MAE %8.3f  80%% coverage %.3f",
            name, overall["crps"], report["crps_vs_best_fixed"][name], best_fixed,
            overall["mae_median"], overall["coverage_80"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
