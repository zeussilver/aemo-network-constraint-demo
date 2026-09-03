"""Train one model per lead-time bucket to produce a forward constraint-risk curve.

The single-horizon model answers "does a constraint bind at any point in the next 6
hours". That is one number for the whole window, so it cannot say *when* inside the
window the risk sits. Lead-time buckets tile the horizon with disjoint labels:

    target_constrained_lead_Xh(t) = 1 if a relevant constraint binds in (t+(X-1)h, t+Xh]

Every bucket is scored from the same origin using the same origin-anchored features, so
one call produces a genuine forward curve without inventing feature values for future
intervals. The bucket configuration reuses the Phase 3 winner (logistic regression on
time_weather_dispatch_lag) rather than re-running the whole feature-set benchmark.

Example:

    python scripts/train_lead_time_models.py \\
      --duid CATHROCK --days 60 --horizon-hours 6 --lead-hours 1 2 3 4 5 6 \\
      --source cache --end-date 2026-05-18 \\
      --model-out artifacts/cathrock_lead_time_models.joblib
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_horizon_model import (  # noqa: E402 - needs the path above
    evaluate_event_windows,
    find_positive_event_windows,
    summarise_backtest,
)
from src.aemo_constraint_demo.features import FEATURE_SET_CHOICES  # noqa: E402
from src.aemo_constraint_demo.frames import INTERVALS_PER_HOUR, build_model_frame  # noqa: E402
from src.aemo_constraint_demo.train import (  # noqa: E402
    DEFAULT_BACKTEST_WINDOW_HOURS,
    DEFAULT_COORDINATE_FILE,
    MODEL_TYPE_CHOICES,
    _fit_probability_model,
    planned_constraint_tables,
    read_standing_table,
    resolve_coordinates,
)

DEFAULT_LEAD_HOURS = (1, 2, 3, 4, 5, 6)


def _json_default(value):  # noqa: ANN001, ANN202 - argparse/JSON glue
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return str(value)


def fit_target_model(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    model_type: str,
) -> tuple[object, dict]:
    """Fit one model on every labelled row and report its training distribution."""
    labelled = frame.dropna(subset=[target_column]).copy()
    labelled[target_column] = labelled[target_column].astype(int)
    if labelled.empty:
        raise ValueError(f"No labelled rows for {target_column}.")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"sklearn\..*")
        model, resolved_model_type, y_train = _fit_probability_model(
            labelled, feature_cols, target_column, model_type=model_type
        )
    stats = {
        "target_column": target_column,
        "model_type": resolved_model_type,
        "requested_model_type": model_type,
        "train_samples": int(len(labelled)),
        "positive_samples": int(y_train.sum()),
        "positive_rate": float(y_train.mean()),
    }
    return model, stats


def evaluate_target(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    duid: str,
    days: int,
    horizon_hours: int,
    model_type: str,
    window_hours: int,
    max_folds: int,
) -> tuple[dict, list[dict]]:
    """Run the shared event-window backtest against one target column."""
    windows = find_positive_event_windows(
        frame, target_column=target_column, window_hours=window_hours, max_folds=max_folds
    )
    records = evaluate_event_windows(
        frame, feature_cols=feature_cols, target_column=target_column, windows=windows, model_type=model_type
    )
    summary = summarise_backtest(
        records, duid=duid, days=days, horizon_hours=horizon_hours, target_column=target_column
    )
    return summary, records


def write_report(
    path: Path,
    duid: str,
    days: int,
    horizon_hours: int,
    lead_hours: list[int],
    feature_set: str,
    model_type: str,
    cumulative: dict,
    lead_summaries: dict[int, dict],
    lead_stats: dict[int, dict],
) -> None:
    def metric(summary: dict, key: str) -> str:
        value = summary.get(key)
        return "n/a" if value is None else f"{value:.4f}"

    rows = "\n".join(
        f"| {lead}h | (t+{lead - 1}h, t+{lead}h] | {lead_stats[lead]['positive_samples']} | "
        f"{lead_stats[lead]['positive_rate']:.4f} | {lead_summaries[lead]['evaluated_folds']} | "
        f"{metric(lead_summaries[lead], 'mean_average_precision')} | "
        f"{metric(lead_summaries[lead], 'mean_roc_auc')} | "
        f"{metric(lead_summaries[lead], 'mean_lift_at_top_5_percent')} |"
        for lead in lead_hours
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Lead-time bucket models

Target: `{duid.upper()}`, {days} days, horizon `{horizon_hours}h`, feature set
`{feature_set}`, model `{model_type}`.

Each bucket is a disjoint window inside the horizon, labelled
`target_constrained_lead_Xh(t) = 1 if a relevant constraint binds in (t+(X-1)h, t+Xh]`.
Features stay origin-anchored, so every bucket is scored from the same observed state.

## Per-bucket event-window backtest

| Bucket | Window | Positives | Positive rate | Folds | Mean AP | Mean ROC AUC | Mean lift@top-5% |
| --- | --- | --- | --- | --- | --- | --- | --- |
{rows}

## Cumulative horizon reference

The same frame, labelled `{cumulative['target_column']}` (bind anywhere in the horizon):

```text
evaluated_folds: {cumulative['evaluated_folds']}
mean_average_precision: {metric(cumulative, 'mean_average_precision')}
mean_roc_auc: {metric(cumulative, 'mean_roc_auc')}
mean_lift_at_top_5_percent: {metric(cumulative, 'mean_lift_at_top_5_percent')}
```

## Reading this honestly

Bucket labels are sparser than the cumulative label by construction - a 6h horizon
positive becomes a positive in only the bucket that contains the event - so per-bucket
folds are fewer and the metrics are noisier. Buckets whose folds were all skipped report
`n/a` rather than a fabricated number. This is a demonstration that the forward curve can
be produced without train/serve skew, not evidence of production-grade lead-time accuracy.

Generated at {datetime.now(timezone.utc).isoformat()}.
""",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict:
    lead_hours = sorted({int(lead) for lead in args.lead_hours})
    if max(lead_hours) > args.horizon_hours:
        raise ValueError(
            f"--lead-hours must fall inside --horizon-hours={args.horizon_hours}; got {lead_hours}."
        )

    latitude, longitude, coordinate_metadata = resolve_coordinates(
        duid=args.duid,
        latitude=args.lat,
        longitude=args.lon,
        coordinate_file=args.coordinate_file,
        no_weather=args.no_weather,
    )

    print("Downloading standing-data tables...", flush=True)
    standing_source = "auto" if args.source == "network" else args.source
    dudetail = read_standing_table(
        "DUDETAIL", source=standing_source, year=args.static_year, month=args.static_month, cache_dir=args.cache_dir
    )
    spd_cp = read_standing_table(
        "SPDCONNECTIONPOINTCONSTRAINT",
        source=standing_source,
        year=args.static_year,
        month=args.static_month,
        cache_dir=args.cache_dir,
    )

    print(f"Building model frame from {args.days} trading days ({args.source})...", flush=True)
    model_frame = build_model_frame(
        duid=args.duid,
        days=args.days,
        horizon_hours=args.horizon_hours,
        feature_set=args.feature_set,
        lead_hours=lead_hours,
        bind_threshold=args.bind_threshold,
        no_weather=args.no_weather,
        latitude=latitude,
        longitude=longitude,
        cache_dir=args.cache_dir,
        end_date=args.end_date,
        source=args.source,
        dudetail=dudetail,
        spd_connectionpoint=spd_cp,
    )
    frame = model_frame.frame
    feature_cols = model_frame.feature_columns
    print(
        f"{len(frame)} labelled rows, {len(feature_cols)} features, "
        f"connection point {model_frame.connection_point}",
        flush=True,
    )

    lead_models: dict[int, object] = {}
    lead_stats: dict[int, dict] = {}
    lead_summaries: dict[int, dict] = {}
    fold_records: list[dict] = []
    for lead in lead_hours:
        target_column = model_frame.lead_target_columns[lead]
        print(f"Bucket {lead}h -> {target_column}...", flush=True)
        summary, records = evaluate_target(
            frame,
            feature_cols=feature_cols,
            target_column=target_column,
            duid=args.duid,
            days=args.days,
            horizon_hours=args.horizon_hours,
            model_type=args.model_type,
            window_hours=args.backtest_window_hours,
            max_folds=args.max_folds,
        )
        model, stats = fit_target_model(frame, feature_cols, target_column, args.model_type)
        lead_models[lead] = model
        lead_stats[lead] = stats
        lead_summaries[lead] = summary
        for record in records:
            fold_records.append({"lead_hours": lead, **record})

    print("Cumulative horizon model...", flush=True)
    cumulative_summary, cumulative_records = evaluate_target(
        frame,
        feature_cols=feature_cols,
        target_column=model_frame.target_column,
        duid=args.duid,
        days=args.days,
        horizon_hours=args.horizon_hours,
        model_type=args.model_type,
        window_hours=args.backtest_window_hours,
        max_folds=args.max_folds,
    )
    cumulative_model, cumulative_stats = fit_target_model(
        frame, feature_cols, model_frame.target_column, args.model_type
    )
    for record in cumulative_records:
        fold_records.append({"lead_hours": None, **record})

    trained_at = datetime.now(timezone.utc).isoformat()
    metrics = {
        "duid": args.duid.upper(),
        "days": int(args.days),
        "horizon_hours": int(args.horizon_hours),
        "lead_hours": lead_hours,
        "lead_bucket_hours": 1,
        "feature_set": args.feature_set,
        "model_type": cumulative_stats["model_type"],
        "connection_point_id": model_frame.connection_point,
        "relevant_genconid_count": len(model_frame.relevant_genconids),
        "features": feature_cols,
        "total_samples": int(len(frame)),
        "coordinate_source": coordinate_metadata,
        "cumulative": {**cumulative_stats, "backtest": cumulative_summary},
        "leads": {
            str(lead): {**lead_stats[lead], "backtest": lead_summaries[lead]} for lead in lead_hours
        },
        "trained_at": trained_at,
    }

    metrics_path = Path(args.metrics_out)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2, default=_json_default), encoding="utf-8")

    folds_path = Path(args.folds_out)
    folds_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fold_records).to_csv(folds_path, index=False)

    planned = planned_constraint_tables(
        model_frame.relevant_genconids,
        static_year=args.static_year,
        static_month=args.static_month,
        cache_dir=args.cache_dir,
        source=standing_source,
    )
    bundle = {
        # The cumulative model stays under "model" so single-horizon serving is unchanged.
        "model": cumulative_model,
        "lead_models": lead_models,
        "lead_hours": lead_hours,
        "lead_bucket_hours": 1,
        "feature_columns": feature_cols,
        "duid": args.duid.upper(),
        "connection_point": model_frame.connection_point,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "coordinate_source": coordinate_metadata,
        "relevant_genconids": model_frame.relevant_genconids,
        "lhs_rows": model_frame.lhs_rows,
        **planned,
        "metrics": {**cumulative_stats, **cumulative_summary, "lead_hours": lead_hours},
        "lead_metrics": {lead: {**lead_stats[lead], **lead_summaries[lead]} for lead in lead_hours},
        "trained_at_utc": trained_at,
        "horizon_hours": int(args.horizon_hours),
        "target_column": model_frame.target_column,
        "lead_target_columns": model_frame.lead_target_columns,
        "bind_threshold": args.bind_threshold,
        "include_dispatch_features": False,
        "feature_set": args.feature_set,
        "model_type": cumulative_stats["model_type"],
        "requested_model_type": args.model_type,
        "no_weather": bool(args.no_weather),
    }
    model_path = Path(args.model_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path)

    if args.report_out:
        write_report(
            Path(args.report_out),
            duid=args.duid,
            days=args.days,
            horizon_hours=args.horizon_hours,
            lead_hours=lead_hours,
            feature_set=args.feature_set,
            model_type=cumulative_stats["model_type"],
            cumulative=cumulative_summary,
            lead_summaries=lead_summaries,
            lead_stats=lead_stats,
        )

    result = {
        "model_out": str(model_path),
        "metrics_out": str(metrics_path),
        "folds_out": str(folds_path),
        "report_out": args.report_out,
        "duid": args.duid.upper(),
        "lead_hours": lead_hours,
        "leads": {
            lead: {
                "positive_samples": lead_stats[lead]["positive_samples"],
                "evaluated_folds": lead_summaries[lead]["evaluated_folds"],
                "mean_average_precision": lead_summaries[lead]["mean_average_precision"],
                "mean_lift_at_top_5_percent": lead_summaries[lead]["mean_lift_at_top_5_percent"],
            }
            for lead in lead_hours
        },
        "cumulative": {
            "evaluated_folds": cumulative_summary["evaluated_folds"],
            "mean_average_precision": cumulative_summary["mean_average_precision"],
            "mean_lift_at_top_5_percent": cumulative_summary["mean_lift_at_top_5_percent"],
        },
    }
    print(json.dumps(result, indent=2, default=_json_default), flush=True)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train lead-time bucket models for a DUID constraint forecast.")
    parser.add_argument("--duid", required=True)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--horizon-hours", type=int, default=6)
    parser.add_argument("--lead-hours", type=int, nargs="+", default=list(DEFAULT_LEAD_HOURS))
    parser.add_argument("--feature-set", default="time_weather_dispatch_lag", choices=list(FEATURE_SET_CHOICES))
    parser.add_argument("--model-type", default="logistic_regression", choices=list(MODEL_TYPE_CHOICES))
    parser.add_argument("--bind-threshold", type=float, default=0.0)
    parser.add_argument("--backtest-window-hours", type=int, default=DEFAULT_BACKTEST_WINDOW_HOURS)
    parser.add_argument("--max-folds", type=int, default=5)
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--source", default="network", choices=["auto", "cache", "network"])
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--static-year", type=int, default=None)
    parser.add_argument("--static-month", type=int, default=None)
    parser.add_argument("--coordinate-file", default=str(DEFAULT_COORDINATE_FILE))
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lon", type=float, default=None)
    parser.add_argument("--no-weather", action="store_true")
    parser.add_argument("--model-out", default="artifacts/lead_time_models.joblib")
    parser.add_argument("--metrics-out", default="artifacts/lead_time_metrics.json")
    parser.add_argument("--folds-out", default="artifacts/lead_time_folds.csv")
    parser.add_argument("--report-out", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
