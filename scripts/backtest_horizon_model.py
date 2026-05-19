#!/usr/bin/env python
"""Event-window backtesting for the horizon-based CATHROCK demo model."""
from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import joblib
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.aemo_constraint_demo.aemo_client import load_next_day_dispatch_tables, read_monthly_table
from src.aemo_constraint_demo.features import (
    add_time_features,
    build_label_frame,
    numericise_feature_columns,
    relevant_constraints_for_duid,
    select_feature_columns,
)
from src.aemo_constraint_demo.scan import add_horizon_labels, horizon_label_column
from src.aemo_constraint_demo.train import (
    DEFAULT_BACKTEST_WINDOW_HOURS,
    DEFAULT_COORDINATE_FILE,
    INTERVALS_PER_HOUR,
    _fit_probability_model,
    _format_timestamp,
    _safe_metric,
    resolve_coordinates,
)
from src.aemo_constraint_demo.weather import fetch_open_meteo_history, merge_weather_to_intervals


@dataclass(frozen=True)
class ValidationWindow:
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp


def _json_default(value):
    if isinstance(value, pd.Timestamp):
        return _format_timestamp(value)
    return str(value)


def _is_target_like_column(column: str) -> bool:
    normalized = str(column).strip().lower()
    return normalized == "target_constrained" or normalized.startswith("target_constrained_next_")


def safe_feature_columns(frame: pd.DataFrame, include_dispatch_features: bool = False) -> list[str]:
    """Return train.py-compatible features with an explicit target-column guard."""
    return [
        col
        for col in select_feature_columns(frame, include_dispatch_features=include_dispatch_features)
        if not _is_target_like_column(col)
    ]


def precision_and_lift_at_top_fraction(y_true: Iterable[int], y_score: Iterable[float], fraction: float) -> tuple[float | None, float | None]:
    truth = pd.Series(y_true).astype(int).reset_index(drop=True)
    scores = pd.Series(y_score).reset_index(drop=True)
    if truth.empty:
        return None, None

    top_n = max(1, math.ceil(len(scores) * fraction))
    top_index = scores.sort_values(ascending=False).index[:top_n]
    precision = float(truth.iloc[top_index].mean())
    base_rate = float(truth.mean())
    lift = None if base_rate <= 0 else float(precision / base_rate)
    return precision, lift


def find_positive_event_windows(
    frame: pd.DataFrame,
    target_column: str,
    window_hours: int = DEFAULT_BACKTEST_WINDOW_HOURS,
    max_folds: int = 5,
) -> list[ValidationWindow]:
    """Build latest-first validation windows ending at distinct positive runs."""
    working = frame.dropna(subset=[target_column, "SETTLEMENTDATE"]).copy()
    if working.empty:
        return []

    working["SETTLEMENTDATE"] = pd.to_datetime(working["SETTLEMENTDATE"], errors="coerce")
    working = working.dropna(subset=["SETTLEMENTDATE"]).sort_values("SETTLEMENTDATE").reset_index(drop=True)
    working[target_column] = working[target_column].astype(int)
    positives = working[working[target_column] == 1].copy()
    if positives.empty:
        return []

    gaps = positives["SETTLEMENTDATE"].diff().dropna()
    expected_step = gaps.median() if not gaps.empty else pd.Timedelta(minutes=5)
    if pd.isna(expected_step) or expected_step <= pd.Timedelta(0):
        expected_step = pd.Timedelta(minutes=5)
    gap_threshold = max(pd.Timedelta(minutes=10), expected_step * 1.5)

    group_id = positives["SETTLEMENTDATE"].diff().gt(gap_threshold).cumsum()
    event_ends = positives.groupby(group_id)["SETTLEMENTDATE"].max().sort_values(ascending=False)

    windows: list[ValidationWindow] = []
    duration = pd.Timedelta(hours=int(window_hours))
    for event_end in event_ends:
        validation_end = pd.Timestamp(event_end)
        validation_start = validation_end - duration
        windows.append(ValidationWindow(validation_start=validation_start, validation_end=validation_end))
        if len(windows) >= int(max_folds):
            break
    return windows


def evaluate_event_windows(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    windows: list[ValidationWindow],
) -> list[dict]:
    working = frame.dropna(subset=[target_column, "SETTLEMENTDATE"]).copy()
    working["SETTLEMENTDATE"] = pd.to_datetime(working["SETTLEMENTDATE"], errors="coerce")
    working = working.dropna(subset=["SETTLEMENTDATE"]).sort_values("SETTLEMENTDATE").reset_index(drop=True)
    working[target_column] = working[target_column].astype(int)

    records: list[dict] = []
    for fold_index, window in enumerate(windows, start=1):
        train_df = working[working["SETTLEMENTDATE"] < window.validation_start].copy()
        validation_df = working[
            (working["SETTLEMENTDATE"] >= window.validation_start)
            & (working["SETTLEMENTDATE"] <= window.validation_end)
        ].copy()

        base_record = {
            "fold_id": fold_index,
            "validation_start": _format_timestamp(window.validation_start),
            "validation_end": _format_timestamp(window.validation_end),
            "train_samples": int(len(train_df)),
            "validation_samples": int(len(validation_df)),
            "train_positive_rate": None,
            "validation_positive_rate": None,
            "average_precision": None,
            "roc_auc": None,
            "precision_at_top_5_percent": None,
            "precision_at_top_10_percent": None,
            "lift_at_top_5_percent": None,
            "lift_at_top_10_percent": None,
            "model_type": None,
            "skipped": True,
            "skip_reason": None,
            "train_start": _format_timestamp(train_df["SETTLEMENTDATE"].min()) if not train_df.empty else None,
            "train_end": _format_timestamp(train_df["SETTLEMENTDATE"].max()) if not train_df.empty else None,
        }

        if train_df.empty:
            records.append({**base_record, "skip_reason": "no_prior_training_rows"})
            continue
        if validation_df.empty:
            records.append({**base_record, "skip_reason": "no_validation_rows"})
            continue

        y_train = train_df[target_column].astype(int)
        y_validation = validation_df[target_column].astype(int)
        base_record["train_positive_rate"] = float(y_train.mean())
        base_record["validation_positive_rate"] = float(y_validation.mean())

        if y_train.nunique(dropna=True) < 2:
            records.append({**base_record, "skip_reason": "prior_training_has_one_target_class"})
            continue
        if y_validation.nunique(dropna=True) < 2:
            records.append({**base_record, "skip_reason": "validation_has_one_target_class"})
            continue

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"sklearn\..*")
            model, model_type, _ = _fit_probability_model(train_df, feature_cols, target_column)
            scores = model.predict_proba(validation_df[feature_cols])[:, 1]
        precision_5, lift_5 = precision_and_lift_at_top_fraction(y_validation, scores, 0.05)
        precision_10, lift_10 = precision_and_lift_at_top_fraction(y_validation, scores, 0.10)

        records.append(
            {
                **base_record,
                "average_precision": _safe_metric(average_precision_score, y_validation, scores),
                "roc_auc": _safe_metric(roc_auc_score, y_validation, scores),
                "precision_at_top_5_percent": precision_5,
                "precision_at_top_10_percent": precision_10,
                "lift_at_top_5_percent": lift_5,
                "lift_at_top_10_percent": lift_10,
                "model_type": model_type,
                "skipped": False,
                "skip_reason": "",
            }
        )

    return records


def _mean_metric(records: list[dict], key: str) -> float | None:
    values = [record[key] for record in records if not record.get("skipped") and record.get(key) is not None]
    if not values:
        return None
    return float(sum(values) / len(values))


def summarise_backtest(
    records: list[dict],
    duid: str,
    days: int,
    horizon_hours: int,
    target_column: str,
) -> dict:
    evaluated = [record for record in records if not record.get("skipped")]
    skipped = [record for record in records if record.get("skipped")]
    limitations = [
        "Event-window folds are selected from positive horizon-label periods and are not a replacement for a long-run production backtest.",
        "The main chronological holdout can still have no positive labels; undefined AP/AUC should remain null.",
        "Features remain limited to the existing demo time/weather feature set unless --include-dispatch-features is used.",
    ]
    if not evaluated:
        limitations.append("No valid two-class event-window folds were available for evaluation.")
    if skipped:
        limitations.append("Some candidate folds were skipped because their training or validation target distribution was invalid.")

    return {
        "duid": duid.upper(),
        "days": int(days),
        "horizon_hours": int(horizon_hours),
        "target_column": target_column,
        "evaluated_folds": int(len(evaluated)),
        "skipped_folds": int(len(skipped)),
        "mean_average_precision": _mean_metric(records, "average_precision"),
        "mean_roc_auc": _mean_metric(records, "roc_auc"),
        "mean_precision_at_top_5_percent": _mean_metric(records, "precision_at_top_5_percent"),
        "mean_precision_at_top_10_percent": _mean_metric(records, "precision_at_top_10_percent"),
        "mean_lift_at_top_5_percent": _mean_metric(records, "lift_at_top_5_percent"),
        "mean_lift_at_top_10_percent": _mean_metric(records, "lift_at_top_10_percent"),
        "limitations": limitations,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_backtest_frame(
    duid: str,
    days: int,
    horizon_hours: int,
    cache_dir: str | Path = "data/cache",
    coordinate_file: str | Path = DEFAULT_COORDINATE_FILE,
    static_year: int | None = None,
    static_month: int | None = None,
    bind_threshold: float = 0.0,
    include_dispatch_features: bool = False,
    no_weather: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
) -> tuple[pd.DataFrame, list[str], str, dict]:
    latitude, longitude, coordinate_metadata = resolve_coordinates(
        duid=duid,
        latitude=latitude,
        longitude=longitude,
        coordinate_file=coordinate_file,
        no_weather=no_weather,
    )

    dudetail = read_monthly_table("DUDETAIL", year=static_year, month=static_month, cache_dir=cache_dir)
    spd_cp = read_monthly_table(
        "SPDCONNECTIONPOINTCONSTRAINT", year=static_year, month=static_month, cache_dir=cache_dir
    )
    relevant, connection_point, _ = relevant_constraints_for_duid(duid, dudetail, spd_cp)

    dispatch = load_next_day_dispatch_tables(
        days=days,
        table_names=["DISPATCHLOAD", "DISPATCHCONSTRAINT"],
        cache_dir=cache_dir,
    )
    frame = build_label_frame(
        dispatchload=dispatch["DISPATCHLOAD"],
        dispatchconstraint=dispatch["DISPATCHCONSTRAINT"],
        duid=duid,
        relevant_genconids=relevant,
        bind_threshold=bind_threshold,
    )

    target_column = horizon_label_column(horizon_hours)
    frame = add_horizon_labels(frame, [horizon_hours], intervals_per_hour=INTERVALS_PER_HOUR)
    dropped_incomplete_horizon_rows = min(len(frame), int(horizon_hours) * INTERVALS_PER_HOUR)
    if dropped_incomplete_horizon_rows:
        tail_index = frame.tail(dropped_incomplete_horizon_rows).index
        frame.loc[tail_index, target_column] = pd.NA
    frame = add_time_features(frame)

    if not no_weather:
        start = frame["SETTLEMENTDATE"].min().date()
        end = frame["SETTLEMENTDATE"].max().date()
        weather = fetch_open_meteo_history(latitude, longitude, start, end)
        frame = merge_weather_to_intervals(frame, weather)

    feature_cols = safe_feature_columns(frame, include_dispatch_features=include_dispatch_features)
    if not feature_cols:
        raise ValueError("No feature columns available after feature engineering.")

    frame = numericise_feature_columns(frame, feature_cols)
    frame = frame.dropna(subset=[target_column, "SETTLEMENTDATE"]).sort_values("SETTLEMENTDATE").reset_index(drop=True)
    metadata = {
        "connection_point": connection_point,
        "relevant_genconid_count": len(relevant),
        "coordinate_source": coordinate_metadata,
        "dropped_incomplete_horizon_rows": dropped_incomplete_horizon_rows,
        "feature_columns": feature_cols,
    }
    return frame, feature_cols, target_column, metadata


def run_backtest(args: argparse.Namespace) -> dict:
    model_bundle = None
    model_path = Path(args.model_out)
    if model_path.exists():
        model_bundle = joblib.load(model_path)

    include_dispatch_features = bool(args.include_dispatch_features)
    no_weather = bool(args.no_weather)
    latitude = args.lat
    longitude = args.lon
    if model_bundle is not None:
        include_dispatch_features = bool(model_bundle.get("include_dispatch_features", include_dispatch_features))
        no_weather = bool(model_bundle.get("no_weather", no_weather))
        latitude = model_bundle.get("latitude", latitude)
        longitude = model_bundle.get("longitude", longitude)

    frame, feature_cols, target_column, metadata = build_backtest_frame(
        duid=args.duid,
        days=args.days,
        horizon_hours=args.horizon_hours,
        cache_dir=args.cache_dir,
        coordinate_file=args.coordinate_file,
        static_year=args.static_year,
        static_month=args.static_month,
        bind_threshold=args.bind_threshold,
        include_dispatch_features=include_dispatch_features,
        no_weather=no_weather,
        latitude=latitude,
        longitude=longitude,
    )
    windows = find_positive_event_windows(
        frame=frame,
        target_column=target_column,
        window_hours=args.validation_window_hours,
        max_folds=args.max_folds,
    )
    records = evaluate_event_windows(frame=frame, feature_cols=feature_cols, target_column=target_column, windows=windows)
    summary = summarise_backtest(
        records=records,
        duid=args.duid,
        days=args.days,
        horizon_hours=args.horizon_hours,
        target_column=target_column,
    )
    summary.update(
        {
            "model_bundle": str(model_path),
            "validation_window_hours": int(args.validation_window_hours),
            "connection_point": metadata["connection_point"],
            "relevant_genconid_count": metadata["relevant_genconid_count"],
            "feature_columns": feature_cols,
            "total_rows": int(len(frame)),
            "positive_rows": int(frame[target_column].sum()),
            "positive_rate": float(frame[target_column].mean()) if len(frame) else None,
        }
    )

    out = Path(args.out)
    folds_out = Path(args.folds_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    folds_out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    pd.DataFrame(records).to_csv(folds_out, index=False)
    return {"summary": summary, "folds": records, "summary_path": str(out), "folds_path": str(folds_out)}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run event-window backtests for a DUID horizon model.")
    parser.add_argument("--duid", required=True)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--horizon-hours", type=int, required=True)
    parser.add_argument("--model-out", required=True, help="Existing trained model bundle used for metadata consistency.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--folds-out", required=True)
    parser.add_argument("--max-folds", type=int, default=5)
    parser.add_argument("--validation-window-hours", type=int, default=DEFAULT_BACKTEST_WINDOW_HOURS)
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--coordinate-file", default=str(DEFAULT_COORDINATE_FILE))
    parser.add_argument("--static-year", type=int, default=None)
    parser.add_argument("--static-month", type=int, default=None)
    parser.add_argument("--bind-threshold", type=float, default=0.0)
    parser.add_argument("--include-dispatch-features", action="store_true")
    parser.add_argument("--no-weather", action="store_true")
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lon", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_backtest(args)
    print(json.dumps(result["summary"], indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
