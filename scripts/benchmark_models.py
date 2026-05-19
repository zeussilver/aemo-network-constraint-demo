#!/usr/bin/env python
"""Benchmark Phase 3 feature sets and model types for sparse constraint risk."""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import joblib
import pandas as pd
from sklearn.inspection import permutation_importance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_horizon_model import (  # noqa: E402
    build_backtest_frame,
    evaluate_event_windows,
    find_positive_event_windows,
)
from src.aemo_constraint_demo.features import FEATURE_SET_CHOICES, select_feature_columns  # noqa: E402
from src.aemo_constraint_demo.train import (  # noqa: E402
    DEFAULT_BACKTEST_WINDOW_HOURS,
    DEFAULT_COORDINATE_FILE,
    MODEL_TYPE_CHOICES,
    train_demo_model,
)

BENCHMARK_COLUMNS = [
    "model",
    "feature_set",
    "mean_average_precision",
    "mean_precision_at_top_5_percent",
    "mean_lift_at_top_5_percent",
    "mean_roc_auc",
    "mean_precision_at_top_10_percent",
    "mean_lift_at_top_10_percent",
    "mean_calibration_brier_score",
    "evaluated_folds",
    "skipped_folds",
    "skip_reasons",
    "feature_count",
    "target_column",
    "days",
    "horizon_hours",
    "total_rows",
    "positive_rows",
    "positive_rate",
]


def _json_default(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _mean_metric(records: list[dict], key: str) -> float | None:
    values = [record[key] for record in records if not record.get("skipped") and record.get(key) is not None]
    if not values:
        return None
    return float(sum(values) / len(values))


def _skip_reasons(records: list[dict]) -> str:
    reasons = sorted({str(record.get("skip_reason", "")).strip() for record in records if record.get("skipped")})
    return ";".join(reason for reason in reasons if reason)


def summarise_combination(
    records: list[dict],
    model_type: str,
    feature_set: str,
    feature_cols: list[str],
    target_column: str,
    frame: pd.DataFrame,
    days: int,
    horizon_hours: int,
    no_candidate_windows: bool = False,
) -> dict:
    evaluated = [record for record in records if not record.get("skipped")]
    skipped = [record for record in records if record.get("skipped")]
    skip_reasons = _skip_reasons(records)
    if no_candidate_windows:
        skip_reasons = "no_candidate_event_windows"
    return {
        "model": model_type,
        "feature_set": feature_set,
        "mean_average_precision": _mean_metric(records, "average_precision"),
        "mean_precision_at_top_5_percent": _mean_metric(records, "precision_at_top_5_percent"),
        "mean_lift_at_top_5_percent": _mean_metric(records, "lift_at_top_5_percent"),
        "mean_roc_auc": _mean_metric(records, "roc_auc"),
        "mean_precision_at_top_10_percent": _mean_metric(records, "precision_at_top_10_percent"),
        "mean_lift_at_top_10_percent": _mean_metric(records, "lift_at_top_10_percent"),
        "mean_calibration_brier_score": _mean_metric(records, "calibration_brier_score"),
        "evaluated_folds": int(len(evaluated)),
        "skipped_folds": int(len(skipped) if not no_candidate_windows else 1),
        "skip_reasons": skip_reasons,
        "feature_count": int(len(feature_cols)),
        "target_column": target_column,
        "days": int(days),
        "horizon_hours": int(horizon_hours),
        "total_rows": int(len(frame)),
        "positive_rows": int(frame[target_column].sum()) if target_column in frame.columns else 0,
        "positive_rate": float(frame[target_column].mean()) if target_column in frame.columns and len(frame) else None,
    }


def evaluate_combination(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    windows: list,
    model_type: str,
    feature_set: str,
    days: int,
    horizon_hours: int,
) -> tuple[dict, list[dict]]:
    if not windows:
        row = summarise_combination(
            records=[],
            model_type=model_type,
            feature_set=feature_set,
            feature_cols=feature_cols,
            target_column=target_column,
            frame=frame,
            days=days,
            horizon_hours=horizon_hours,
            no_candidate_windows=True,
        )
        return row, []

    records = evaluate_event_windows(
        frame=frame,
        feature_cols=feature_cols,
        target_column=target_column,
        windows=windows,
        model_type=model_type,
    )
    row = summarise_combination(
        records=records,
        model_type=model_type,
        feature_set=feature_set,
        feature_cols=feature_cols,
        target_column=target_column,
        frame=frame,
        days=days,
        horizon_hours=horizon_hours,
    )
    return row, records


def select_best_row(benchmark: pd.DataFrame) -> dict:
    if benchmark.empty:
        raise ValueError("No benchmark rows were generated.")
    candidates = benchmark[benchmark["evaluated_folds"] > 0].copy()
    if candidates.empty:
        candidates = benchmark.copy()

    candidates["_ap_sort"] = candidates["mean_average_precision"].fillna(-1.0)
    candidates["_p5_sort"] = candidates["mean_precision_at_top_5_percent"].fillna(-1.0)
    candidates["_lift5_sort"] = candidates["mean_lift_at_top_5_percent"].fillna(-1.0)
    candidates["_roc_sort"] = candidates["mean_roc_auc"].fillna(-1.0)
    candidates["_brier_sort"] = candidates["mean_calibration_brier_score"].fillna(float("inf"))
    candidates = candidates.sort_values(
        ["_ap_sort", "_p5_sort", "_lift5_sort", "_roc_sort", "_brier_sort"],
        ascending=[False, False, False, False, True],
        kind="mergesort",
    )
    clean = candidates.iloc[0].drop(labels=[col for col in candidates.columns if col.startswith("_")])
    return clean.to_dict()


def _classifier_from_bundle(bundle: dict):
    model = bundle.get("model")
    if hasattr(model, "named_steps"):
        return model.named_steps.get("classifier")
    return model


def write_feature_importance(
    bundle_path: str | Path,
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    out_path: str | Path,
    permutation_sample_size: int = 2000,
) -> Path:
    bundle = joblib.load(bundle_path)
    model = bundle["model"]
    classifier = _classifier_from_bundle(bundle)
    rows: list[dict] = []

    if hasattr(classifier, "coef_"):
        coefficients = classifier.coef_[0]
        for feature, value in zip(feature_cols, coefficients, strict=False):
            rows.append(
                {
                    "feature": feature,
                    "importance_type": "coefficient",
                    "importance": float(value),
                    "absolute_importance": abs(float(value)),
                }
            )

    if hasattr(classifier, "feature_importances_"):
        for feature, value in zip(feature_cols, classifier.feature_importances_, strict=False):
            rows.append(
                {
                    "feature": feature,
                    "importance_type": "model_feature_importance",
                    "importance": float(value),
                    "absolute_importance": abs(float(value)),
                }
            )

    working = frame.dropna(subset=[target_column]).copy()
    if feature_cols and working[target_column].nunique(dropna=True) >= 2:
        sample_size = min(int(permutation_sample_size), len(working))
        if sample_size > 0:
            sample = working.sample(n=sample_size, random_state=42) if len(working) > sample_size else working
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"sklearn\..*")
                result = permutation_importance(
                    model,
                    sample[feature_cols],
                    sample[target_column].astype(int),
                    n_repeats=3,
                    random_state=42,
                    scoring="average_precision",
                )
            for feature, mean_value, std_value in zip(
                feature_cols,
                result.importances_mean,
                result.importances_std,
                strict=False,
            ):
                rows.append(
                    {
                        "feature": feature,
                        "importance_type": "permutation_average_precision",
                        "importance": float(mean_value),
                        "absolute_importance": abs(float(mean_value)),
                        "importance_std": float(std_value),
                    }
                )

    if not rows:
        rows.append(
            {
                "feature": "",
                "importance_type": "not_available",
                "importance": None,
                "absolute_importance": None,
                "note": "Feature importance unavailable for this model or one-class target frame.",
            }
        )

    importance = pd.DataFrame(rows)
    importance["rank"] = (
        importance["absolute_importance"].fillna(-1).rank(method="first", ascending=False).astype(int)
    )
    importance = importance.sort_values(["rank", "feature", "importance_type"])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    importance.to_csv(out, index=False)
    return out


def _read_phase2_reference() -> dict | None:
    path = Path("artifacts/cathrock_6h_backtest_summary.json")
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _format_metric(value) -> str:
    if value is None or pd.isna(value):
        return "null"
    return f"{float(value):.6f}"


def write_report(
    report_out: str | Path,
    benchmark: pd.DataFrame,
    best_row: dict,
    command: str,
    feature_importance_out: str | Path,
    phase2_reference: dict | None,
) -> Path:
    report = Path(report_out)
    report.parent.mkdir(parents=True, exist_ok=True)
    best_ap = best_row.get("mean_average_precision")
    phase2_ap = phase2_reference.get("mean_average_precision") if phase2_reference else None
    improved = bool(best_ap is not None and phase2_ap is not None and float(best_ap) > float(phase2_ap))

    summary_columns = [
        "model",
        "feature_set",
        "mean_average_precision",
        "mean_precision_at_top_5_percent",
        "mean_lift_at_top_5_percent",
        "mean_roc_auc",
        "evaluated_folds",
        "skipped_folds",
    ]
    table = benchmark[summary_columns].to_string(index=False)
    text = f"""# Phase 3 Performance Report

Timestamp: `{datetime.now(timezone.utc).isoformat()}`

## Benchmark Command

```bash
{command}
```

## Evaluated Settings

Feature sets: `{', '.join(benchmark['feature_set'].drop_duplicates().astype(str).tolist())}`

Model types: `{', '.join(benchmark['model'].drop_duplicates().astype(str).tolist())}`

## Benchmark Summary

```text
{table}
```

## Best Setting

- best_model: `{best_row.get('model')}`
- best_feature_set: `{best_row.get('feature_set')}`
- mean_average_precision: `{_format_metric(best_row.get('mean_average_precision'))}`
- mean_precision_at_top_5_percent: `{_format_metric(best_row.get('mean_precision_at_top_5_percent'))}`
- mean_lift_at_top_5_percent: `{_format_metric(best_row.get('mean_lift_at_top_5_percent'))}`
- mean_roc_auc: `{_format_metric(best_row.get('mean_roc_auc'))}`
- evaluated_folds: `{int(best_row.get('evaluated_folds', 0))}`
- skipped_folds: `{int(best_row.get('skipped_folds', 0))}`

## Phase 2 Comparison

- Phase 2.5 reference AP: `{_format_metric(phase2_ap)}`
- Phase 3 best AP: `{_format_metric(best_ap)}`
- improved_over_phase2_5_event_window_ap: `{str(improved).lower()}`

## Outputs

- benchmark_csv: `artifacts/performance_benchmark.csv`
- best_model_bundle: `artifacts/cathrock_6h_best_model.joblib`
- feature_importance_csv: `{feature_importance_out}`

## Test Result

Filled after final validation.

## Limitations

- Event-window folds are sparse and selected around positive periods.
- The chronological holdout may still have no positive labels.
- Lagged constraint features improve backtests only if equivalent current/past operational signals are available at forecast time.
- P5MIN/Predispatch, outage, regional demand, and interconnector features remain future production extensions.
"""
    report.write_text(text, encoding="utf-8")
    return report


def _command_from_args(args: argparse.Namespace) -> str:
    parts = [
        "python scripts/benchmark_models.py",
        f"--duid {args.duid}",
        f"--days {args.days}",
        f"--horizon-hours {args.horizon_hours}",
        "--feature-sets " + " ".join(args.feature_sets),
        "--model-types " + " ".join(args.model_types),
        f"--out {args.out}",
        f"--best-model-out {args.best_model_out}",
        f"--report-out {args.report_out}",
    ]
    return " \\\n  ".join(parts)


def _base_feature_set(feature_sets: Iterable[str]) -> str:
    requested = set(feature_sets)
    if any("dispatch" in item for item in requested) and any("constraint" in item for item in requested):
        return "time_weather_dispatch_constraint_lag"
    if any("dispatch" in item for item in requested):
        return "time_weather_dispatch_lag"
    if any("constraint" in item for item in requested):
        return "time_weather_constraint_lag"
    if any(item != "time_only" for item in requested):
        return "time_weather"
    return "time_only"


def run_benchmark(args: argparse.Namespace) -> dict:
    rows: list[dict] = []
    fold_records: dict[str, list[dict]] = {}
    frames: dict[str, tuple[pd.DataFrame, list[str], str, dict]] = {}

    base_feature_set = _base_feature_set(args.feature_sets)
    print(f"Building benchmark frame with base feature set {base_feature_set}...", flush=True)
    base_frame, _, target_column, base_metadata = build_backtest_frame(
        duid=args.duid,
        days=args.days,
        horizon_hours=args.horizon_hours,
        cache_dir=args.cache_dir,
        coordinate_file=args.coordinate_file,
        static_year=args.static_year,
        static_month=args.static_month,
        bind_threshold=args.bind_threshold,
        include_dispatch_features=args.include_dispatch_features,
        feature_set=base_feature_set,
        no_weather=args.no_weather,
        latitude=args.lat,
        longitude=args.lon,
    )
    windows = find_positive_event_windows(
        frame=base_frame,
        target_column=target_column,
        window_hours=args.validation_window_hours,
        max_folds=args.max_folds,
    )

    for feature_set in args.feature_sets:
        feature_cols = select_feature_columns(
            base_frame,
            include_dispatch_features=args.include_dispatch_features,
            feature_set=feature_set,
        )
        frames[feature_set] = (base_frame, feature_cols, target_column, {**base_metadata, "feature_set": feature_set})

        for model_type in args.model_types:
            print(f"Evaluating feature_set={feature_set} model_type={model_type}...", flush=True)
            row, records = evaluate_combination(
                frame=base_frame,
                feature_cols=feature_cols,
                target_column=target_column,
                windows=windows,
                model_type=model_type,
                feature_set=feature_set,
                days=args.days,
                horizon_hours=args.horizon_hours,
            )
            rows.append(row)
            fold_records[f"{feature_set}:{model_type}"] = records

    benchmark = pd.DataFrame(rows)
    benchmark = benchmark.reindex(columns=BENCHMARK_COLUMNS)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    benchmark.to_csv(out, index=False)

    best_row = select_best_row(benchmark)
    best_feature_set = str(best_row["feature_set"])
    best_model_type = str(best_row["model"])

    best_result = train_demo_model(
        duid=args.duid,
        latitude=args.lat,
        longitude=args.lon,
        days=args.days,
        cache_dir=args.cache_dir,
        model_out=args.best_model_out,
        metrics_out=args.best_model_metrics_out,
        coordinate_file=args.coordinate_file,
        static_year=args.static_year,
        static_month=args.static_month,
        bind_threshold=args.bind_threshold,
        horizon_hours=args.horizon_hours,
        backtest_window_hours=args.validation_window_hours,
        include_dispatch_features=args.include_dispatch_features,
        feature_set=best_feature_set,
        model_type=best_model_type,
        no_weather=args.no_weather,
    )

    best_frame, best_feature_cols, best_target_column, _ = frames[best_feature_set]
    importance_out = write_feature_importance(
        bundle_path=args.best_model_out,
        frame=best_frame,
        feature_cols=best_feature_cols,
        target_column=best_target_column,
        out_path=args.feature_importance_out,
        permutation_sample_size=args.importance_sample_size,
    )

    report = write_report(
        report_out=args.report_out,
        benchmark=benchmark,
        best_row=best_row,
        command=_command_from_args(args),
        feature_importance_out=importance_out,
        phase2_reference=_read_phase2_reference(),
    )

    return {
        "benchmark_path": str(out),
        "best_row": best_row,
        "best_model_result": best_result,
        "feature_importance_path": str(importance_out),
        "report_path": str(report),
        "fold_records": fold_records,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Phase 3 AEMO constraint model settings.")
    parser.add_argument("--duid", required=True)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--horizon-hours", type=int, required=True)
    parser.add_argument("--feature-sets", nargs="+", choices=FEATURE_SET_CHOICES, required=True)
    parser.add_argument("--model-types", nargs="+", choices=MODEL_TYPE_CHOICES, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--best-model-out", required=True)
    parser.add_argument("--best-model-metrics-out", default=None)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--feature-importance-out", default="artifacts/cathrock_6h_feature_importance.csv")
    parser.add_argument("--max-folds", type=int, default=5)
    parser.add_argument("--validation-window-hours", type=int, default=DEFAULT_BACKTEST_WINDOW_HOURS)
    parser.add_argument("--importance-sample-size", type=int, default=2000)
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
    result = run_benchmark(args)
    print(json.dumps({k: v for k, v in result.items() if k != "fold_records"}, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
