"""Train a small DUID network-constraint forecaster."""
from __future__ import annotations

import argparse
import json
import math
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, classification_report, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .aemo_client import (
    load_next_day_dispatch_tables,
    read_monthly_table,
    read_monthly_table_cached_first,
)
from .features import FEATURE_SET_CHOICES, numericise_feature_columns
from .frames import build_model_frame
from .models import ConstantProbabilityClassifier

DEFAULT_COORDINATE_FILE = Path("data/duid_coordinates.csv")
INTERVALS_PER_HOUR = 12
DEFAULT_BACKTEST_WINDOW_HOURS = 24
MODEL_TYPE_CHOICES = ("logistic_regression", "random_forest", "hist_gradient_boosting")


def _safe_metric(func, y_true, y_score):
    try:
        if pd.Series(y_true).nunique(dropna=True) < 2:
            return None
        return float(func(y_true, y_score))
    except Exception:  # noqa: BLE001 - metric undefined when test has one class
        return None


def _precision_at_top_fraction(y_true, y_score, fraction: float = 0.05) -> float | None:
    if len(y_true) == 0:
        return None
    truth = pd.Series(y_true).astype(int).reset_index(drop=True)
    scores = pd.Series(y_score).reset_index(drop=True)
    top_n = max(1, math.ceil(len(scores) * fraction))
    top_index = scores.sort_values(ascending=False).index[:top_n]
    return float(truth.iloc[top_index].mean())


def _default_metrics_out(model_out: Path) -> Path:
    return model_out.with_name(f"{model_out.stem}_metrics.json")


def _classifier_for_target(y_train: pd.Series, model_type: str = "logistic_regression") -> tuple[object, str]:
    if model_type not in MODEL_TYPE_CHOICES:
        raise ValueError(f"Unknown model_type {model_type!r}. Choose one of {list(MODEL_TYPE_CHOICES)}.")
    train_classes = sorted(pd.Series(y_train).unique().tolist())
    if len(train_classes) < 2:
        constant_probability = float(pd.Series(y_train).mean())
        print(
            "Training window contains one target class only; using a constant-probability baseline model.",
            flush=True,
        )
        return ConstantProbabilityClassifier(probability=constant_probability), "constant_probability_baseline"
    if model_type == "logistic_regression":
        return LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42), model_type
    if model_type == "random_forest":
        return (
            RandomForestClassifier(
                n_estimators=80,
                min_samples_leaf=2,
                class_weight="balanced_subsample",
                random_state=42,
                n_jobs=-1,
            ),
            model_type,
        )
    return (
        HistGradientBoostingClassifier(
            max_iter=100,
            learning_rate=0.05,
            l2_regularization=0.1,
            random_state=42,
        ),
        model_type,
    )


def _build_pipeline(classifier: object, model_type: str) -> Pipeline:
    steps: list[tuple[str, object]] = [("imputer", SimpleImputer(strategy="median"))]
    if model_type in {"logistic_regression", "constant_probability_baseline"}:
        steps.append(("scaler", StandardScaler()))
    steps.append(("classifier", classifier))
    return Pipeline(steps=steps)


def _fit_probability_model(
    train_df: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    model_type: str = "logistic_regression",
) -> tuple[Pipeline, str, pd.Series]:
    y_train = train_df[target_column].astype(int)
    classifier, resolved_model_type = _classifier_for_target(y_train, model_type=model_type)
    model = _build_pipeline(classifier, model_type=resolved_model_type)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"sklearn\..*")
        model.fit(train_df[feature_cols], y_train)
    return model, resolved_model_type, y_train


def _evaluate_predictions(y_true: pd.Series, y_score, threshold: float = 0.5) -> dict:
    y_true = pd.Series(y_true).astype(int)
    y_pred = (y_score >= threshold).astype(int)
    return {
        "positive_rate": float(y_true.mean()),
        "average_precision": _safe_metric(average_precision_score, y_true, y_score),
        "roc_auc": _safe_metric(roc_auc_score, y_true, y_score),
        "precision_at_top_5_percent": _precision_at_top_fraction(y_true, y_score, fraction=0.05),
        "calibration_brier_score": float(brier_score_loss(y_true, y_score)),
        "classification_report": classification_report(y_true, y_pred, zero_division=0, output_dict=True),
    }


def _format_timestamp(value: pd.Timestamp) -> str | None:
    if pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


def supplemental_positive_backtest(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_column: str,
    window_hours: int = DEFAULT_BACKTEST_WINDOW_HOURS,
    model_type: str = "logistic_regression",
) -> dict:
    """Evaluate the latest positive validation window with only prior rows for training."""
    window_hours = int(window_hours)
    base = {
        "available": False,
        "window_hours": window_hours,
        "selection": "latest_positive_window_with_prior_two_class_training",
    }
    if window_hours <= 0:
        return {**base, "reason": "disabled"}

    working = frame.dropna(subset=[target_column, "SETTLEMENTDATE"]).copy()
    if working.empty:
        return {**base, "reason": "no rows available"}

    working["SETTLEMENTDATE"] = pd.to_datetime(working["SETTLEMENTDATE"], errors="coerce")
    working = working.dropna(subset=["SETTLEMENTDATE"]).sort_values("SETTLEMENTDATE").reset_index(drop=True)
    working[target_column] = working[target_column].astype(int)

    positive_times = working.loc[working[target_column] == 1, "SETTLEMENTDATE"].dropna().sort_values().unique()
    if len(positive_times) == 0:
        return {**base, "reason": "no positive labels available"}

    for value in reversed(positive_times):
        validation_end = pd.Timestamp(value)
        validation_start = validation_end - pd.Timedelta(hours=window_hours)
        train_df = working[working["SETTLEMENTDATE"] <= validation_start].copy()
        validation_df = working[
            (working["SETTLEMENTDATE"] > validation_start) & (working["SETTLEMENTDATE"] <= validation_end)
        ].copy()
        if train_df.empty or validation_df.empty:
            continue
        if train_df[target_column].nunique() < 2:
            continue
        if validation_df[target_column].nunique() < 2 or int(validation_df[target_column].sum()) <= 0:
            continue

        model, resolved_model_type, y_train = _fit_probability_model(
            train_df,
            feature_cols,
            target_column,
            model_type=model_type,
        )
        y_validation = validation_df[target_column].astype(int)
        y_score = model.predict_proba(validation_df[feature_cols])[:, 1]
        evaluation = _evaluate_predictions(y_validation, y_score)
        return {
            **base,
            "available": True,
            "model_type": resolved_model_type,
            "train_start": _format_timestamp(train_df["SETTLEMENTDATE"].min()),
            "train_end": _format_timestamp(train_df["SETTLEMENTDATE"].max()),
            "validation_start": _format_timestamp(validation_df["SETTLEMENTDATE"].min()),
            "validation_end": _format_timestamp(validation_df["SETTLEMENTDATE"].max()),
            "train_samples": int(len(train_df)),
            "validation_samples": int(len(validation_df)),
            "train_positive_rate": float(y_train.mean()),
            "validation_positive_rate": evaluation["positive_rate"],
            "train_positive_samples": int(y_train.sum()),
            "validation_positive_samples": int(y_validation.sum()),
            "average_precision": evaluation["average_precision"],
            "roc_auc": evaluation["roc_auc"],
            "precision_at_top_5_percent": evaluation["precision_at_top_5_percent"],
            "calibration_brier_score": evaluation["calibration_brier_score"],
            "classification_report": evaluation["classification_report"],
        }

    return {**base, "reason": "no positive validation window found with prior two-class training"}


def load_duid_coordinate(duid: str, coordinate_file: str | Path = DEFAULT_COORDINATE_FILE) -> dict | None:
    """Load a vetted coordinate row for a DUID, if present."""
    path = Path(coordinate_file)
    if not path.exists():
        return None

    coordinates = pd.read_csv(path)
    coordinates.columns = [str(col).strip().lower() for col in coordinates.columns]
    required = {"duid", "lat", "lon"}
    missing = required - set(coordinates.columns)
    if missing:
        raise ValueError(f"{path} is missing coordinate columns: {sorted(missing)}")

    rows = coordinates[coordinates["duid"].astype(str).str.upper().str.strip() == duid.upper()].copy()
    if rows.empty:
        return None

    row = rows.iloc[0]
    lat = pd.to_numeric(pd.Series([row["lat"]]), errors="coerce").iloc[0]
    lon = pd.to_numeric(pd.Series([row["lon"]]), errors="coerce").iloc[0]
    if pd.isna(lat) or pd.isna(lon):
        raise ValueError(f"{path} has invalid coordinates for DUID {duid.upper()}.")
    return {
        "latitude": float(lat),
        "longitude": float(lon),
        "source": str(row.get("source", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
    }


def resolve_coordinates(
    duid: str,
    latitude: float | None,
    longitude: float | None,
    coordinate_file: str | Path = DEFAULT_COORDINATE_FILE,
    no_weather: bool = False,
) -> tuple[float, float, dict]:
    """Resolve CLI or vetted-file coordinates for weather-backed features."""
    if latitude is not None and longitude is not None:
        return float(latitude), float(longitude), {"source": "cli", "notes": "--lat/--lon override"}
    if latitude is not None or longitude is not None:
        raise ValueError("Pass both --lat and --lon, or omit both to use data/duid_coordinates.csv.")

    coordinate = load_duid_coordinate(duid, coordinate_file=coordinate_file)
    if coordinate is not None:
        metadata = {"source": coordinate["source"], "notes": coordinate["notes"], "coordinate_file": str(coordinate_file)}
        return coordinate["latitude"], coordinate["longitude"], metadata

    if no_weather:
        return 0.0, 0.0, {"source": "not_used_no_weather", "notes": "Coordinates omitted because --no-weather was set."}

    raise ValueError(
        f"No vetted coordinates found for {duid.upper()} in {coordinate_file}. "
        "Pass --lat and --lon manually or add a verified row to the coordinate file."
    )


def read_standing_table(
    table: str,
    source: str = "network",
    year: int | None = None,
    month: int | None = None,
    cache_dir: str | Path = "data/cache",
) -> pd.DataFrame:
    """Read a monthly standing-data table, optionally preferring the local cache.

    The default keeps training on the network path. Offline callers pass "cache" or
    "auto", which matters because NEMWeb publishes a month's archive some weeks after
    that month starts.
    """
    if source == "network":
        return read_monthly_table(table, year=year, month=month, cache_dir=cache_dir)
    return read_monthly_table_cached_first(table, year=year, month=month, cache_dir=cache_dir, source=source)


def planned_constraint_tables(
    relevant_genconids: "list[str] | set[str]",
    static_year: int | None = None,
    static_month: int | None = None,
    cache_dir: str | Path = "data/cache",
    source: str = "network",
) -> dict[str, pd.DataFrame]:
    """Return the planned/invoked constraint rows relevant to one DUID.

    Only relevant rows are kept, so a model bundle can carry the planned-constraint view
    without carrying whole monthly standing-data tables.
    """
    relevant = {str(item).upper() for item in relevant_genconids}
    tables = {}
    for name in ("GENCONDATA", "GENCONSET", "GENCONSETINVOKE"):
        table = read_standing_table(
            name, source=source, year=static_year, month=static_month, cache_dir=cache_dir
        ).copy()
        table.columns = [str(col).strip().upper() for col in table.columns]
        tables[name] = table

    def _by_genconid(table: pd.DataFrame) -> pd.DataFrame:
        column = table.get("GENCONID", pd.Series(index=table.index, dtype=str))
        return table[column.astype(str).str.upper().isin(relevant)].copy()

    relevant_gdata = _by_genconid(tables["GENCONDATA"])
    relevant_gset = _by_genconid(tables["GENCONSET"])

    relevant_sets: set[str] = set()
    if not relevant_gset.empty and "GENCONSETID" in relevant_gset.columns:
        relevant_sets = set(relevant_gset["GENCONSETID"].astype(str).str.upper())
    invocations = tables["GENCONSETINVOKE"]
    if relevant_sets and "GENCONSETID" in invocations.columns:
        relevant_invocations = invocations[
            invocations["GENCONSETID"].astype(str).str.upper().isin(relevant_sets)
        ].copy()
    else:
        relevant_invocations = pd.DataFrame()

    return {
        "planned_gencondata": relevant_gdata,
        "planned_genconset": relevant_gset,
        "planned_genconsetinvoke": relevant_invocations,
    }


def train_demo_model(
    duid: str,
    latitude: float | None = None,
    longitude: float | None = None,
    days: int = 21,
    cache_dir: str | Path = "data/cache",
    model_out: str | Path = "artifacts/model.joblib",
    metrics_out: str | Path | None = None,
    coordinate_file: str | Path = DEFAULT_COORDINATE_FILE,
    static_year: int | None = None,
    static_month: int | None = None,
    bind_threshold: float = 0.0,
    horizon_hours: int = 0,
    backtest_window_hours: int = DEFAULT_BACKTEST_WINDOW_HOURS,
    include_dispatch_features: bool = False,
    feature_set: str = "time_weather",
    model_type: str = "logistic_regression",
    no_weather: bool = False,
) -> dict:
    cache_dir = Path(cache_dir)
    model_out = Path(model_out)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    metrics_path = Path(metrics_out) if metrics_out is not None else _default_metrics_out(model_out)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    horizon_hours = int(horizon_hours)
    if horizon_hours < 0:
        raise ValueError("horizon_hours must be non-negative.")
    if feature_set not in FEATURE_SET_CHOICES:
        raise ValueError(f"Unknown feature_set {feature_set!r}. Choose one of {list(FEATURE_SET_CHOICES)}.")
    if model_type not in MODEL_TYPE_CHOICES:
        raise ValueError(f"Unknown model_type {model_type!r}. Choose one of {list(MODEL_TYPE_CHOICES)}.")
    latitude, longitude, coordinate_metadata = resolve_coordinates(
        duid=duid,
        latitude=latitude,
        longitude=longitude,
        coordinate_file=coordinate_file,
        no_weather=no_weather,
    )

    print("Downloading standing-data tables...", flush=True)
    dudetail = read_monthly_table("DUDETAIL", year=static_year, month=static_month, cache_dir=cache_dir)
    spd_cp = read_monthly_table(
        "SPDCONNECTIONPOINTCONSTRAINT", year=static_year, month=static_month, cache_dir=cache_dir
    )
    print(f"Downloading and parsing latest {days} Next Day Dispatch files...", flush=True)
    dispatch = load_next_day_dispatch_tables(
        days=days,
        table_names=["DISPATCHLOAD", "DISPATCHCONSTRAINT"],
        cache_dir=cache_dir,
    )

    model_frame = build_model_frame(
        duid=duid,
        days=days,
        horizon_hours=horizon_hours,
        feature_set=feature_set,
        include_dispatch_features=include_dispatch_features,
        bind_threshold=bind_threshold,
        no_weather=no_weather,
        latitude=latitude,
        longitude=longitude,
        cache_dir=cache_dir,
        dudetail=dudetail,
        spd_connectionpoint=spd_cp,
        dispatchload=dispatch["DISPATCHLOAD"],
        dispatchconstraint=dispatch["DISPATCHCONSTRAINT"],
    )
    frame = model_frame.frame
    feature_cols = model_frame.feature_columns
    target_column = model_frame.target_column
    relevant = model_frame.relevant_genconids
    connection_point = model_frame.connection_point
    lhs_rows = model_frame.lhs_rows
    dropped_incomplete_horizon_rows = model_frame.metadata["dropped_incomplete_horizon_rows"]
    print(f"DUID {duid} maps to connection point {connection_point}; relevant GENCONIDs: {len(relevant)}", flush=True)

    split = max(1, int(len(frame) * 0.8))
    train_df = frame.iloc[:split].copy()
    test_df = frame.iloc[split:].copy()
    if test_df.empty:
        raise ValueError("Not enough rows for a train/test split. Increase --days.")

    model, resolved_model_type, y_train = _fit_probability_model(
        train_df,
        feature_cols,
        target_column,
        model_type=model_type,
    )
    y_test = test_df[target_column].astype(int)
    y_score = model.predict_proba(test_df[feature_cols])[:, 1]
    trained_at = datetime.now(timezone.utc).isoformat()
    test_evaluation = _evaluate_predictions(y_test, y_score)
    supplemental_backtest = supplemental_positive_backtest(
        frame=frame,
        feature_cols=feature_cols,
        target_column=target_column,
        window_hours=backtest_window_hours,
        model_type=model_type,
    )

    metrics = {
        "duid": duid.upper(),
        "days": int(days),
        "horizon_hours": int(horizon_hours),
        "target_column": target_column,
        "connection_point_id": connection_point,
        "relevant_genconid_count": len(relevant),
        "model_type": resolved_model_type,
        "requested_model_type": model_type,
        "feature_set": feature_set,
        "total_samples": int(len(frame)),
        "train_samples": int(len(train_df)),
        "test_samples": int(len(test_df)),
        "positive_rate": float(frame[target_column].mean()),
        "train_positive_rate": float(y_train.mean()),
        "test_positive_rate": float(y_test.mean()),
        "average_precision": test_evaluation["average_precision"],
        "roc_auc": test_evaluation["roc_auc"],
        "precision_at_top_5_percent": test_evaluation["precision_at_top_5_percent"],
        "calibration_brier_score": test_evaluation["calibration_brier_score"],
        "supplemental_positive_backtest": supplemental_backtest,
        "features": feature_cols,
        "trained_at": trained_at,
        "dropped_incomplete_horizon_rows": dropped_incomplete_horizon_rows,
        "coordinate_source": coordinate_metadata,
        "rows": int(len(frame)),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "test_average_precision": test_evaluation["average_precision"],
        "test_roc_auc": test_evaluation["roc_auc"],
        "classification_report": test_evaluation["classification_report"],
    }
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    planned = planned_constraint_tables(
        relevant, static_year=static_year, static_month=static_month, cache_dir=cache_dir
    )

    bundle = {
        "model": model,
        "feature_columns": feature_cols,
        "duid": duid.upper(),
        "connection_point": connection_point,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "coordinate_source": coordinate_metadata,
        "relevant_genconids": relevant,
        "lhs_rows": lhs_rows,
        **planned,
        "metrics": metrics,
        "trained_at_utc": trained_at,
        "horizon_hours": int(horizon_hours),
        "target_column": target_column,
        "metrics_out": str(metrics_path),
        "bind_threshold": bind_threshold,
        "include_dispatch_features": include_dispatch_features,
        "feature_set": feature_set,
        "model_type": resolved_model_type,
        "requested_model_type": model_type,
        "no_weather": no_weather,
    }
    joblib.dump(bundle, model_out)

    result = {
        "model_out": str(model_out),
        "metrics_out": str(metrics_path),
        "duid": duid.upper(),
        "connection_point": connection_point,
        "horizon_hours": int(horizon_hours),
        "target_column": target_column,
        "relevant_genconids_count": len(relevant),
        "feature_columns": feature_cols,
        "metrics": metrics,
        "top_relevant_genconids": relevant[:15],
    }
    print(json.dumps(result, indent=2, default=str), flush=True)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train an AEMO DUID constraint forecast demo model.")
    parser.add_argument("--duid", required=True, help="Dispatchable Unit ID, e.g. OAKEY1SF or ARWF1")
    parser.add_argument("--lat", type=float, default=None, help="Latitude for weather features; overrides coordinate file")
    parser.add_argument("--lon", type=float, default=None, help="Longitude for weather features; overrides coordinate file")
    parser.add_argument("--days", type=int, default=21, help="Number of recent Next Day Dispatch daily files")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--model-out", default="artifacts/model.joblib")
    parser.add_argument("--metrics-out", default=None, help="Optional metrics JSON output path")
    parser.add_argument("--coordinate-file", default=str(DEFAULT_COORDINATE_FILE), help="Vetted DUID coordinate CSV")
    parser.add_argument("--static-year", type=int, default=None, help="Optional MMSDM standing-data year")
    parser.add_argument("--static-month", type=int, default=None, help="Optional MMSDM standing-data month")
    parser.add_argument("--bind-threshold", type=float, default=0.0)
    parser.add_argument("--horizon-hours", type=int, default=0, help="Future-only target horizon in hours; 0 uses current interval")
    parser.add_argument(
        "--backtest-window-hours",
        type=int,
        default=DEFAULT_BACKTEST_WINDOW_HOURS,
        help="Supplemental positive held-out validation window length in hours; 0 disables it",
    )
    parser.add_argument("--include-dispatch-features", action="store_true")
    parser.add_argument("--feature-set", choices=FEATURE_SET_CHOICES, default="time_weather")
    parser.add_argument("--model-type", choices=MODEL_TYPE_CHOICES, default="logistic_regression")
    parser.add_argument("--no-weather", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    train_demo_model(
        duid=args.duid,
        latitude=args.lat,
        longitude=args.lon,
        days=args.days,
        cache_dir=args.cache_dir,
        model_out=args.model_out,
        metrics_out=args.metrics_out,
        coordinate_file=args.coordinate_file,
        static_year=args.static_year,
        static_month=args.static_month,
        bind_threshold=args.bind_threshold,
        horizon_hours=args.horizon_hours,
        backtest_window_hours=args.backtest_window_hours,
        include_dispatch_features=args.include_dispatch_features,
        feature_set=args.feature_set,
        model_type=args.model_type,
        no_weather=args.no_weather,
    )


if __name__ == "__main__":
    main()
