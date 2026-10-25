"""Assemble a modelling table from the price lane's files, one row per forecast opportunity.

A row is "at 14:30 AEMO published a forecast for the half hour ending 18:00 in NSW1", and
it carries everything that was knowable at 14:30 plus the price that interval actually
settled at. That shape is what both the point and the probabilistic models need, and it is
what makes an honest backtest possible: nothing on a row may postdate ``published``.

Three groups of columns:

- **What AEMO published for that interval**: the base scenario price (``RRPEEP37``, the
  column that matches AEMO's published forecast price most often — see
  `docs/price-lane-data.md`) and the shape of the 43 scenarios around it, which is AEMO's
  own statement of how uncertain the forecast is.
- **What the market had just done**: the last settled price before publication and rolling
  statistics over the preceding hours, joined as-of ``published`` so a row can never see a
  price from after it. Demand and the semi-scheduled wind and solar share come the same way.
- **When it is**: half hour of the day, day of the week, month — a price curve is strongly
  periodic.

The target is the 30-minute trading price: the mean of the six five-minute prices in the
interval, which is the resolution AEMO forecasts at.

    python -m src.aemo_constraint_demo.price_dataset --region NSW1
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

log = logging.getLogger("price_dataset")

TRADING_INTERVAL = "30min"
# Scenario columns are raw only for the regions the backfill was asked to keep them for.
BASE_SCENARIO = "rrpeep37"

SUMMARY_FEATURES = (
    "p05", "p10", "p25", "p50", "p75", "p90", "p95",
    "min", "max", "mean", "std", "iqr", "range",
    "share_negative", "share_above_300",
)
LAG_WINDOWS = {"1h": 12, "6h": 72, "24h": 288}  # in five-minute intervals


def trading_end(stamps: pd.Series) -> pd.Series:
    """The half hour a five-minute interval belongs to, named by the time it ends."""
    return stamps.dt.ceil(TRADING_INTERVAL)


def read_many(paths: Sequence[Path], region: str | None = None, **kwargs) -> pd.DataFrame:
    """Read and concatenate files, dropping other regions per file to bound memory.

    A year of sensitivity summaries is 4.85 million rows across all five regions; reading
    them whole before filtering is what makes this step run out of memory.
    """
    frames = []
    for path in sorted(paths):
        frame = pd.read_csv(path, **kwargs)
        if region is not None and "regionid" in frame.columns:
            frame = frame[frame["regionid"] == region]
        frames.append(frame)
    if not frames:
        raise FileNotFoundError("No input files matched.")
    return pd.concat(frames, ignore_index=True)


def load_five_minute(price_dir: Path, region: str) -> pd.DataFrame:
    """Five-minute prices and regional demand for one region, on one time index."""
    prices = read_many(
        list(price_dir.glob("actual_price_*.csv.gz")),
        region=region,
        usecols=["settlementdate", "regionid", "rrp"],
        parse_dates=["settlementdate"],
    )
    demand = read_many(
        list(price_dir.glob("region_demand_*.csv.gz")),
        region=region,
        usecols=[
            "settlementdate", "regionid", "totaldemand", "availablegeneration",
            "ss_solar_clearedmw", "ss_wind_clearedmw",
        ],
        parse_dates=["settlementdate"],
    )
    prices = prices.drop(columns="regionid")
    demand = demand.drop(columns="regionid")
    frame = prices.merge(demand, on="settlementdate", how="left").sort_values("settlementdate")
    vre = frame["ss_solar_clearedmw"].fillna(0) + frame["ss_wind_clearedmw"].fillna(0)
    frame["vre_mw"] = vre
    frame["vre_share"] = (vre / frame["totaldemand"]).replace([np.inf, -np.inf], np.nan)
    return frame.reset_index(drop=True)


def market_state(five_minute: pd.DataFrame) -> pd.DataFrame:
    """What the market looked like as of each five-minute interval, for an as-of join.

    Every column is a backward-looking statistic of intervals that had already settled, so
    joining it at a publication time cannot leak the future.
    """
    frame = five_minute.set_index("settlementdate").sort_index()
    state = pd.DataFrame(index=frame.index)
    state["last_rrp"] = frame["rrp"]
    state["last_demand"] = frame["totaldemand"]
    state["last_vre_share"] = frame["vre_share"]
    state["last_available_generation"] = frame["availablegeneration"]
    for name, window in LAG_WINDOWS.items():
        rolling = frame["rrp"].rolling(window, min_periods=max(2, window // 4))
        state[f"rrp_mean_{name}"] = rolling.mean()
        state[f"rrp_std_{name}"] = rolling.std()
        state[f"rrp_max_{name}"] = rolling.max()
        state[f"demand_mean_{name}"] = (
            frame["totaldemand"].rolling(window, min_periods=max(2, window // 4)).mean()
        )
    state["vre_share_mean_24h"] = frame["vre_share"].rolling(288, min_periods=72).mean()
    state["share_negative_24h"] = (
        (frame["rrp"] < 0).rolling(288, min_periods=72).mean()
    )
    state["share_above_300_24h"] = (
        (frame["rrp"] > 300).rolling(288, min_periods=72).mean()
    )
    return state.reset_index()


def trading_prices(five_minute: pd.DataFrame) -> pd.DataFrame:
    """The 30-minute trading price, which is what AEMO's forecasts are about."""
    frame = five_minute.assign(target=trading_end(five_minute["settlementdate"]))
    grouped = frame.groupby("target").agg(
        actual_rrp=("rrp", "mean"),
        actual_rrp_max=("rrp", "max"),
        actual_rrp_min=("rrp", "min"),
        intervals=("rrp", "size"),
        actual_demand=("totaldemand", "mean"),
        actual_vre_share=("vre_share", "mean"),
    )
    return grouped.reset_index()


def load_forecasts(price_dir: Path, region: str) -> pd.DataFrame:
    """Every published forecast for one region: the scenario summary plus the base case."""
    summary = read_many(
        list(price_dir.glob("sensitivity_summary_*.csv.gz")),
        region=region,
        parse_dates=["published", "datetime"],
    )
    raw_paths = list(price_dir.glob("sensitivity_scenarios_*.csv.gz"))
    if not raw_paths:
        raise FileNotFoundError(
            "No sensitivity_scenarios_*.csv.gz files: re-run the ingest with "
            f"--raw-regions {region} so the base scenario column is available."
        )
    base = read_many(
        raw_paths,
        region=region,
        usecols=["predispatchseqno", "datetime", "regionid", "periodid", BASE_SCENARIO],
        parse_dates=["datetime"],
    ).drop(columns="regionid")
    if base.empty:
        raise ValueError(f"{region} has no raw scenario rows; re-run the ingest for it.")
    merged = summary.merge(
        base.rename(columns={BASE_SCENARIO: "aemo_base"}),
        on=["predispatchseqno", "datetime", "periodid"],
        how="inner",
        validate="one_to_one",
    )
    return merged.rename(columns={"datetime": "target"})


def build_dataset(price_dir: Path, region: str, max_horizon_min: int | None = None) -> pd.DataFrame:
    five_minute = load_five_minute(price_dir, region)
    forecasts = load_forecasts(price_dir, region)

    # A forecast published after the interval it describes was never usable: one late run
    # in the year produces these, and they must not reach a backtest.
    late = int((forecasts["horizon_min"] <= 0).sum())
    forecasts = forecasts[forecasts["horizon_min"] > 0]
    if max_horizon_min is not None:
        forecasts = forecasts[forecasts["horizon_min"] <= max_horizon_min]

    state = market_state(five_minute)
    dataset = pd.merge_asof(
        forecasts.sort_values("published"),
        state.sort_values("settlementdate"),
        left_on="published",
        right_on="settlementdate",
        direction="backward",
        tolerance=pd.Timedelta("1D"),
    )
    dataset = dataset.merge(trading_prices(five_minute), on="target", how="inner")

    dataset["half_hour_of_day"] = (
        dataset["target"].dt.hour * 2 + (dataset["target"].dt.minute >= 30).astype(int)
    )
    dataset["day_of_week"] = dataset["target"].dt.dayofweek
    dataset["month"] = dataset["target"].dt.month
    dataset["horizon_h"] = dataset["horizon_min"] / 60.0
    dataset["base_error"] = dataset["aemo_base"] - dataset["actual_rrp"]
    dataset["regionid"] = region

    log.info(
        "%s: %d rows, %s to %s, %d late forecasts dropped",
        region, len(dataset), dataset["target"].min(), dataset["target"].max(), late,
    )
    return dataset.sort_values(["published", "target"]).reset_index(drop=True)


def feature_columns(dataset: pd.DataFrame) -> list[str]:
    """Model inputs: everything knowable at publication time, and nothing else."""
    lag_columns = [
        column
        for column in dataset.columns
        if column.startswith(("last_", "rrp_mean_", "rrp_std_", "rrp_max_", "demand_mean_"))
        or column in {"vre_share_mean_24h", "share_negative_24h", "share_above_300_24h"}
    ]
    return [
        "aemo_base",
        *[column for column in SUMMARY_FEATURES if column in dataset.columns],
        "horizon_min",
        "half_hour_of_day",
        "day_of_week",
        "month",
        *lag_columns,
    ]


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="NSW1")
    parser.add_argument("--price-dir", default="data/price")
    parser.add_argument("--out", default=None, help="Default: <price-dir>/dataset_<REGION>.csv.gz")
    parser.add_argument("--max-horizon-min", type=int, default=None)
    args = parser.parse_args(argv)

    price_dir = Path(args.price_dir)
    dataset = build_dataset(price_dir, args.region.upper(), args.max_horizon_min)
    out = Path(args.out) if args.out else price_dir / f"dataset_{args.region.upper()}.csv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(out, index=False, compression="gzip")
    log.info("wrote %s (%d rows, %d columns)", out, len(dataset), dataset.shape[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
