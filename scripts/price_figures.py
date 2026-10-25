#!/usr/bin/env python
"""Draw the three figures the price lane's write-up needs.

- **Calibration**: nominal level against observed frequency. A forecast on the diagonal
  tells the truth about its own uncertainty; below it is over-confident.
- **CRPS by horizon**: where each forecast earns or loses, which is the whole argument for
  combining them rather than picking one.
- **A day**: the actual price against the model's 80% band and AEMO's base scenario, at one
  horizon, so the numbers above have a picture attached.

    python scripts/price_figures.py --region NSW1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.price_backtest import FORECASTS, read_predictions
from src.aemo_constraint_demo import price_metrics as metrics
from src.aemo_constraint_demo import price_model as pmodel

log = logging.getLogger("price_figures")

SHOWN = ("scenarios", "persistence", "model")
COLOURS = {
    "scenarios": "#c4622d",
    "scenarios_recentred": "#d9a441",
    "persistence": "#6b7280",
    "model": "#1f6feb",
    "ensemble_weight": "#2f8f5b",
}
LABELS = {
    "scenarios": "AEMO scenarios",
    "scenarios_recentred": "AEMO scenarios, recentred",
    "persistence": "Persistence",
    "model": "Quantile model",
    "ensemble_weight": "Adaptive ensemble",
}


def style():
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "font.size": 9,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    return plt


def calibration_figure(scored: pd.DataFrame, names: tuple[str, ...], out: Path, region: str) -> Path:
    plt = style()
    actual = scored["actual_rrp"].to_numpy(dtype=float)
    figure, axis = plt.subplots(figsize=(4.6, 4.2))
    axis.plot([0, 1], [0, 1], color="black", linewidth=0.8, linestyle="--", label="perfect")
    for name in names:
        table = metrics.reliability_table(actual, read_predictions(scored, name))
        axis.plot(
            [row["nominal"] for row in table],
            [row["empirical"] for row in table],
            marker="o",
            markersize=3.5,
            linewidth=1.4,
            color=COLOURS.get(name),
            label=LABELS.get(name, name),
        )
    axis.set_xlabel("quantile the forecast claimed")
    axis.set_ylabel("share of prices that actually fell below it")
    axis.set_title(f"{region}: does the forecast mean what it says?", fontsize=10)
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.legend(frameon=False, fontsize=8, loc="upper left")
    figure.tight_layout()
    figure.savefig(out, bbox_inches="tight")
    plt.close(figure)
    return out


def horizon_figure(scored: pd.DataFrame, names: tuple[str, ...], out: Path, region: str) -> Path:
    plt = style()
    buckets = [name for name, _, _ in pmodel.HORIZON_BUCKETS if (scored["horizon_bucket"] == name).any()]
    figure, axis = plt.subplots(figsize=(6.2, 3.6))
    width = 0.8 / len(names)
    positions = np.arange(len(buckets))
    for index, name in enumerate(names):
        values = []
        for bucket in buckets:
            rows = scored["horizon_bucket"] == bucket
            values.append(
                metrics.crps_from_quantiles(
                    scored.loc[rows, "actual_rrp"].to_numpy(dtype=float),
                    {level: values_[rows.to_numpy()] for level, values_ in read_predictions(scored, name).items()},
                )
            )
        axis.bar(
            positions + index * width - 0.4 + width / 2,
            values,
            width=width,
            color=COLOURS.get(name),
            label=LABELS.get(name, name),
        )
    axis.set_xticks(positions, buckets)
    axis.set_xlabel("how far ahead the forecast was published")
    axis.set_ylabel("CRPS ($/MWh, lower is better)")
    axis.set_title(f"{region}: where each forecast earns its keep", fontsize=10)
    axis.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    figure.savefig(out, bbox_inches="tight")
    plt.close(figure)
    return out


def day_figure(
    scored: pd.DataFrame, out: Path, region: str, horizon_min: int = 720, day: str | None = None
) -> Path:
    """One day of forecasts made at a fixed horizon, with the 80% band drawn."""
    plt = style()
    near = scored[(scored["horizon_min"] - horizon_min).abs() <= 30].copy()
    if near.empty:
        raise ValueError(f"No rows near a {horizon_min}-minute horizon to draw.")
    near["day"] = near["target"].dt.floor("D")
    if day is None:
        # the most interesting day at this horizon: the widest spread between actuals
        spread = near.groupby("day")["actual_rrp"].agg(lambda values: values.max() - values.min())
        chosen = spread.sort_values().index[-1]
    else:
        chosen = pd.Timestamp(day, tz=near["target"].dt.tz)
    frame = near[near["day"] == chosen].sort_values("target")

    figure, axis = plt.subplots(figsize=(6.6, 3.6))
    axis.fill_between(
        frame["target"], frame["model_q10"], frame["model_q90"],
        color=COLOURS["model"], alpha=0.18, label="quantile model, 80% band",
    )
    axis.plot(frame["target"], frame["model_q50"], color=COLOURS["model"], linewidth=1.4, label="quantile model, median")
    axis.plot(frame["target"], frame["aemo_base"], color=COLOURS["scenarios"], linewidth=1.2, label="AEMO base scenario")
    axis.plot(frame["target"], frame["actual_rrp"], color="black", linewidth=1.6, label="settled price")
    axis.set_ylabel("$/MWh")
    axis.set_xlabel(f"{pd.Timestamp(chosen).date()} (NEM time), forecasts published about {horizon_min // 60} h ahead")
    axis.set_title(f"{region}: one day, one horizon", fontsize=10)
    axis.legend(frameon=False, fontsize=8)
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(out, bbox_inches="tight")
    plt.close(figure)
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="NSW1")
    parser.add_argument("--price-dir", default="data/price")
    parser.add_argument("--predictions", default=None)
    parser.add_argument("--out-dir", default="docs/images")
    parser.add_argument("--horizon-min", type=int, default=720)
    parser.add_argument("--day", default=None, help="Day to draw (default: the most eventful one).")
    args = parser.parse_args(argv)

    region = args.region.upper()
    path = Path(args.predictions) if args.predictions else Path(args.price_dir) / f"backtest_predictions_{region}.csv.gz"
    scored = pd.read_csv(path, parse_dates=["published", "target"])
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = tuple(name for name in SHOWN if f"{name}_q50" in scored.columns)

    made = [
        calibration_figure(scored, names, out_dir / f"price-calibration-{region}.png", region),
        horizon_figure(scored, names, out_dir / f"price-crps-by-horizon-{region}.png", region),
        day_figure(scored, out_dir / f"price-day-{region}.png", region, args.horizon_min, args.day),
    ]
    for figure in made:
        log.info("wrote %s", figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
