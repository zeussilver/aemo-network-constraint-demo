"""Quantile models, market regimes and rolling-origin folds for the price lane.

The question is not only "what will the price be" but "how wrong could that be", so every
forecast here is a set of quantiles rather than one number, and every baseline is written
in the same form so they can be scored against each other.

Four forecasts are built:

- ``scenarios``: AEMO's 43 published scenario prices, read as a distribution exactly as
  published. It costs nothing to produce and is already public, so it is the bar any model
  here has to clear.
- ``scenarios_recentred``: the same spread, shifted so that the base scenario sits at its
  median. The published price sits at the 33rd percentile of the raw scenarios
  (`docs/price-lane-data.md`), so the shape is informative while its location is not.
- ``persistence``: the last settled price, with a spread taken from how wrong persistence
  was on the training data at the same horizon. A naive forecast that is honest about its
  own error is surprisingly hard to beat at short horizons.
- ``model``: gradient-boosted quantile regression on publication-time features.

Regimes are labelled from what was knowable when the forecast was published, never from
the interval's own outcome, so a regime-aware forecast could actually have been made. The
thresholds come from the training fold only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

log = logging.getLogger("price_model")

QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
REGIMES = ("high_vre", "peak_demand", "volatile", "normal")

HORIZON_BUCKETS = (
    ("0-2h", 0, 120),
    ("2-6h", 120, 360),
    ("6-12h", 360, 720),
    ("12-24h", 720, 1440),
    ("24h+", 1440, 10**9),
)


# ---------------------------------------------------------------------------------------
# Folds
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    label: str  # the test month, e.g. "2026-04"
    train: np.ndarray
    test: np.ndarray


def monthly_folds(
    dataset: pd.DataFrame,
    n_folds: int = 6,
    min_train_months: int = 3,
    time_column: str = "published",
    target_column: str | None = "target",
) -> list[Fold]:
    """Expanding-window folds: train on everything published before a month, test on it.

    Rolling origin rather than a random split, because a price model that has seen next
    week cannot be evaluated on this week.

    The folds are also **purged**. An interval is forecast up to 67 times, from runs
    published over the previous day and a half, so a forecast published in March can be a
    training row whose label is an April interval — the very outcome the April fold is
    meant to test. Training rows whose interval falls inside the test month are dropped,
    which costs about 2% of them and removes the overlap entirely.
    """
    months = dataset[time_column].dt.strftime("%Y-%m")
    ordered = sorted(months.unique())
    candidates = ordered[min_train_months:]
    folds = []
    for month in candidates[-n_folds:]:
        before = (months < month).to_numpy()
        if target_column is not None:
            month_start = pd.Timestamp(f"{month}-01", tz=dataset[target_column].dt.tz)
            before = before & (dataset[target_column] < month_start).to_numpy()
        train = np.flatnonzero(before)
        test = np.flatnonzero((months == month).to_numpy())
        if len(train) and len(test):
            folds.append(Fold(label=month, train=train, test=test))
    return folds


# ---------------------------------------------------------------------------------------
# Regimes
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegimeThresholds:
    vre_share: float
    demand: float
    volatility: float

    def as_dict(self) -> dict[str, float]:
        return {"vre_share": self.vre_share, "demand": self.demand, "volatility": self.volatility}


def fit_regime_thresholds(train: pd.DataFrame, quantile: float = 0.75) -> RegimeThresholds:
    """Thresholds from the training fold only, so a label never depends on the test period."""
    return RegimeThresholds(
        vre_share=float(train["last_vre_share"].quantile(quantile)),
        demand=float(train["last_demand"].quantile(quantile)),
        volatility=float(train["rrp_std_24h"].quantile(quantile)),
    )


def regime_labels(frame: pd.DataFrame, thresholds: RegimeThresholds) -> pd.Series:
    """One regime per row, from publication-time conditions.

    Order matters: a volatile market is the hardest case and is labelled first, then high
    renewable output, then high demand. Everything else is the ordinary market.
    """
    labels = pd.Series("normal", index=frame.index, dtype=object)
    high_demand = frame["last_demand"] >= thresholds.demand
    high_vre = frame["last_vre_share"] >= thresholds.vre_share
    volatile = frame["rrp_std_24h"] >= thresholds.volatility
    labels[high_demand] = "peak_demand"
    labels[high_vre] = "high_vre"
    labels[volatile] = "volatile"
    return labels


def horizon_bucket(horizon_min: pd.Series) -> pd.Series:
    labels = pd.Series("", index=horizon_min.index, dtype=object)
    for name, low, high in HORIZON_BUCKETS:
        labels[(horizon_min > low) & (horizon_min <= high)] = name
    return labels


# ---------------------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------------------


def enforce_non_crossing(predictions: Mapping[float, np.ndarray]) -> dict[float, np.ndarray]:
    """Sort each row's quantiles, so the 90th percentile is never below the 10th.

    Quantile regressions are fitted one level at a time and nothing ties them together;
    sorting is the standard repair and cannot make any quantile score worse.
    """
    levels = sorted(predictions)
    stacked = np.sort(np.column_stack([np.asarray(predictions[level], dtype=float) for level in levels]), axis=1)
    return {level: stacked[:, index] for index, level in enumerate(levels)}


def scenario_quantiles(frame: pd.DataFrame, levels: Sequence[float] = QUANTILES) -> dict[float, np.ndarray]:
    """AEMO's scenario spread, as published."""
    columns = {level: f"p{int(level * 100):02d}" for level in levels}
    missing = [column for column in columns.values() if column not in frame.columns]
    if missing:
        raise KeyError(f"The dataset has no {missing} columns to read scenarios from.")
    return {level: frame[column].to_numpy(dtype=float) for level, column in columns.items()}


def recentred_scenario_quantiles(
    frame: pd.DataFrame, levels: Sequence[float] = QUANTILES
) -> dict[float, np.ndarray]:
    """The scenario spread moved so the base scenario sits at its median."""
    raw = scenario_quantiles(frame, levels)
    shift = frame["aemo_base"].to_numpy(dtype=float) - raw[0.5]
    return {level: values + shift for level, values in raw.items()}


def persistence_quantiles(
    train: pd.DataFrame, test: pd.DataFrame, levels: Sequence[float] = QUANTILES
) -> dict[float, np.ndarray]:
    """Last settled price, widened by how wrong that was on the training data.

    The error distribution is taken per horizon bucket, because persistence decays: it is
    nearly right half an hour ahead and nearly useless a day ahead.
    """
    train_errors = train["actual_rrp"] - train["last_rrp"]
    buckets = horizon_bucket(train["horizon_min"])
    offsets = {
        bucket: {level: float(group.quantile(level)) for level in levels}
        for bucket, group in train_errors.groupby(buckets)
    }
    overall = {level: float(train_errors.quantile(level)) for level in levels}

    test_buckets = horizon_bucket(test["horizon_min"])
    base = test["last_rrp"].to_numpy(dtype=float)
    out = {}
    for level in levels:
        shifts = test_buckets.map(lambda bucket: offsets.get(bucket, overall)[level]).to_numpy(dtype=float)
        out[level] = base + shifts
    return enforce_non_crossing(out)


def fit_quantile_models(
    features: pd.DataFrame,
    target: pd.Series,
    levels: Sequence[float] = QUANTILES,
    max_iter: int = 300,
    learning_rate: float = 0.08,
    max_leaf_nodes: int = 63,
    random_state: int = 0,
) -> dict[float, HistGradientBoostingRegressor]:
    """One gradient-boosted model per quantile, each minimising its own pinball loss."""
    models = {}
    for level in levels:
        model = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=level,
            max_iter=max_iter,
            learning_rate=learning_rate,
            max_leaf_nodes=max_leaf_nodes,
            early_stopping=False,
            random_state=random_state,
        )
        model.fit(features, target)
        models[level] = model
        log.debug("fitted quantile %.2f on %d rows", level, len(target))
    return models


def predict_quantiles(
    models: Mapping[float, HistGradientBoostingRegressor], features: pd.DataFrame
) -> dict[float, np.ndarray]:
    return enforce_non_crossing({level: model.predict(features) for level, model in models.items()})


def slice_predictions(
    predictions: Mapping[float, np.ndarray], mask: np.ndarray
) -> dict[float, np.ndarray]:
    return {level: np.asarray(values)[mask] for level, values in predictions.items()}
