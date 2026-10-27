#!/usr/bin/env python
"""Rolling-origin backtest of probabilistic price forecasts, written to JSON the docs quote.

Four forecasts are scored on exactly the same rows (see `price_model.py`): AEMO's published
scenario spread, that spread recentred on its base scenario, persistence widened by its own
training error, and gradient-boosted quantile regression on publication-time features.

Each fold trains on everything published before a calendar month and is tested on that
month, so no forecast is ever scored on a period its model had seen. Results are reported
overall, by forecast horizon and by market regime, because a model that wins on average can
still be the wrong one to use in the hours that matter.

    python scripts/price_backtest.py --region NSW1 --out artifacts/price_backtest_NSW1.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.aemo_constraint_demo import price_metrics as metrics
from src.aemo_constraint_demo import price_model as pmodel
from src.aemo_constraint_demo.price_dataset import feature_columns

log = logging.getLogger("price_backtest")

FORECASTS = ("scenarios", "scenarios_recentred", "persistence", "model")


def compact_score(actual: np.ndarray, predictions: dict[float, np.ndarray]) -> dict:
    """The few numbers worth repeating for every slice of the backtest."""
    intervals = {row["nominal"]: row for row in metrics.central_intervals(actual, predictions)}
    eighty = intervals.get(0.8, {})
    return {
        "n": int(len(actual)),
        "crps": round(metrics.crps_from_quantiles(actual, predictions), 4),
        "mae_median": metrics.point_metrics(actual, predictions[0.5])["mae"],
        "coverage_80": eighty.get("coverage"),
        "width_80": eighty.get("mean_width"),
    }


def run_fold(dataset: pd.DataFrame, fold: pmodel.Fold, features: list[str], max_iter: int) -> pd.DataFrame:
    """Fit on the fold's training rows and return its test rows with every forecast attached."""
    train = dataset.iloc[fold.train]
    test = dataset.iloc[fold.test].copy()
    thresholds = pmodel.fit_regime_thresholds(train)

    started = time.perf_counter()
    models = pmodel.fit_quantile_models(train[features], train["actual_rrp"], max_iter=max_iter)
    fitted_in = time.perf_counter() - started

    predictions = {
        "scenarios": pmodel.scenario_quantiles(test),
        "scenarios_recentred": pmodel.recentred_scenario_quantiles(test),
        "persistence": pmodel.persistence_quantiles(train, test),
        "model": pmodel.predict_quantiles(models, test[features]),
    }
    for name, quantiles in predictions.items():
        for level, values in quantiles.items():
            test[f"{name}_q{int(level * 100):02d}"] = values

    test["regime"] = pmodel.regime_labels(test, thresholds)
    test["horizon_bucket"] = pmodel.horizon_bucket(test["horizon_min"])
    test["fold"] = fold.label
    log.info(
        "fold %s: trained on %d rows in %.1fs, scored %d",
        fold.label, len(train), fitted_in, len(test),
    )
    return test


def read_predictions(frame: pd.DataFrame, name: str) -> dict[float, np.ndarray]:
    return {
        level: frame[f"{name}_q{int(level * 100):02d}"].to_numpy(dtype=float)
        for level in pmodel.QUANTILES
    }


def score_everything(scored: pd.DataFrame) -> dict:
    actual = scored["actual_rrp"].to_numpy(dtype=float)
    report: dict = {
        "overall": {name: metrics.score_forecast(actual, read_predictions(scored, name)) for name in FORECASTS},
        "by_horizon": {},
        "by_regime": {},
        "by_fold": {},
    }
    for bucket, group in scored.groupby("horizon_bucket", sort=False):
        report["by_horizon"][str(bucket)] = {
            name: compact_score(group["actual_rrp"].to_numpy(dtype=float), read_predictions(group, name))
            for name in FORECASTS
        }
    for regime, group in scored.groupby("regime", sort=False):
        report["by_regime"][str(regime)] = {
            name: compact_score(group["actual_rrp"].to_numpy(dtype=float), read_predictions(group, name))
            for name in FORECASTS
        }
    for fold, group in scored.groupby("fold", sort=True):
        report["by_fold"][str(fold)] = {
            name: compact_score(group["actual_rrp"].to_numpy(dtype=float), read_predictions(group, name))
            for name in FORECASTS
        }
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="NSW1")
    parser.add_argument("--price-dir", default="data/price")
    parser.add_argument("--dataset", default=None, help="Default: <price-dir>/dataset_<REGION>.csv.gz")
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--min-train-months", type=int, default=3)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--max-horizon-min", type=int, default=None)
    parser.add_argument(
        "--drop-features",
        default="",
        help="Comma-separated features to leave out, for ablations (e.g. the AEMO scenarios).",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--predictions-out", default=None, help="Optional CSV of scored test rows.")
    args = parser.parse_args(argv)

    region = args.region.upper()
    price_dir = Path(args.price_dir)
    path = Path(args.dataset) if args.dataset else price_dir / f"dataset_{region}.csv.gz"
    dataset = pd.read_csv(path, parse_dates=["published", "target", "settlementdate"])
    if args.max_horizon_min is not None:
        dataset = dataset[dataset["horizon_min"] <= args.max_horizon_min]
    dataset = dataset.dropna(subset=["actual_rrp", "aemo_base", "last_rrp"]).reset_index(drop=True)

    dropped = {name.strip() for name in args.drop_features.split(",") if name.strip()}
    features = [
        column
        for column in feature_columns(dataset)
        if column in dataset.columns and column not in dropped
    ]
    folds = pmodel.monthly_folds(dataset, n_folds=args.folds, min_train_months=args.min_train_months)
    if not folds:
        raise SystemExit("Not enough months in the dataset to build a fold.")
    log.info("%s: %d rows, %d features, folds %s", region, len(dataset), len(features),
             [fold.label for fold in folds])

    scored = pd.concat(
        [run_fold(dataset, fold, features, args.max_iter) for fold in folds], ignore_index=True
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": region,
        "dataset": {
            "path": str(path),
            "rows": int(len(dataset)),
            "dropped_features": sorted(dropped),
            "first_published": str(dataset["published"].min()),
            "last_target": str(dataset["target"].max()),
            "features": features,
        },
        "folds": [
            {"label": fold.label, "train_rows": int(len(fold.train)), "test_rows": int(len(fold.test))}
            for fold in folds
        ],
        "quantiles": list(pmodel.QUANTILES),
        "regime_counts": {str(k): int(v) for k, v in scored["regime"].value_counts().items()},
        **score_everything(scored),
    }

    out = Path(args.out) if args.out else Path(f"artifacts/price_backtest_{region}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    log.info("wrote %s", out)
    if args.predictions_out:
        keep = [
            "published", "target", "horizon_min", "horizon_bucket", "regime", "fold",
            "actual_rrp", "aemo_base", "last_rrp",
            *[column for column in scored.columns if any(column.startswith(f"{name}_q") for name in FORECASTS)],
        ]
        scored[keep].to_csv(args.predictions_out, index=False, compression="gzip")
        log.info("wrote %s", args.predictions_out)

    for name in FORECASTS:
        overall = report["overall"][name]
        log.info(
            "%-20s CRPS %8.3f  MAE(median) %8.3f  80%% coverage %.3f",
            name, overall["crps"], overall["point"]["mae"],
            next(row["coverage"] for row in overall["intervals"] if row["nominal"] == 0.8),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
