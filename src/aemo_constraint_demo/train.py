"""Train a small DUID network-constraint forecaster."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, classification_report, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .aemo_client import load_next_day_dispatch_tables, read_monthly_table
from .features import (
    add_time_features,
    build_label_frame,
    numericise_feature_columns,
    relevant_constraints_for_duid,
    select_feature_columns,
)
from .models import ConstantProbabilityClassifier
from .scan import add_horizon_labels, horizon_label_column
from .weather import fetch_open_meteo_history, merge_weather_to_intervals

DEFAULT_COORDINATE_FILE = Path("data/duid_coordinates.csv")
INTERVALS_PER_HOUR = 12


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
    include_dispatch_features: bool = False,
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
    gencondata = read_monthly_table("GENCONDATA", year=static_year, month=static_month, cache_dir=cache_dir)
    genconset = read_monthly_table("GENCONSET", year=static_year, month=static_month, cache_dir=cache_dir)
    genconsetinvoke = read_monthly_table(
        "GENCONSETINVOKE", year=static_year, month=static_month, cache_dir=cache_dir
    )

    relevant, connection_point, lhs_rows = relevant_constraints_for_duid(duid, dudetail, spd_cp)
    print(f"DUID {duid} maps to connection point {connection_point}; relevant GENCONIDs: {len(relevant)}", flush=True)

    print(f"Downloading and parsing latest {days} Next Day Dispatch files...", flush=True)
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
    target_column = "target_constrained"
    dropped_incomplete_horizon_rows = 0
    if horizon_hours > 0:
        frame = add_horizon_labels(frame, [horizon_hours], intervals_per_hour=INTERVALS_PER_HOUR)
        target_column = horizon_label_column(horizon_hours)
        dropped_incomplete_horizon_rows = min(len(frame), horizon_hours * INTERVALS_PER_HOUR)
        if dropped_incomplete_horizon_rows:
            tail_index = frame.tail(dropped_incomplete_horizon_rows).index
            frame.loc[tail_index, target_column] = pd.NA
    frame = add_time_features(frame)

    if not no_weather:
        start = frame["SETTLEMENTDATE"].min().date()
        end = frame["SETTLEMENTDATE"].max().date()
        print(f"Fetching Open-Meteo weather {start} to {end}...", flush=True)
        weather = fetch_open_meteo_history(latitude, longitude, start, end)
        frame = merge_weather_to_intervals(frame, weather)

    feature_cols = select_feature_columns(frame, include_dispatch_features=include_dispatch_features)
    if not feature_cols:
        raise ValueError("No feature columns available after feature engineering.")
    frame = numericise_feature_columns(frame, feature_cols)
    frame = frame.dropna(subset=[target_column, "SETTLEMENTDATE"])
    frame = frame.sort_values("SETTLEMENTDATE").reset_index(drop=True)

    split = max(1, int(len(frame) * 0.8))
    train_df = frame.iloc[:split].copy()
    test_df = frame.iloc[split:].copy()
    if test_df.empty:
        raise ValueError("Not enough rows for a train/test split. Increase --days.")

    X_train, y_train = train_df[feature_cols], train_df[target_column].astype(int)
    X_test, y_test = test_df[feature_cols], test_df[target_column].astype(int)

    train_classes = sorted(y_train.unique().tolist())
    if len(train_classes) < 2:
        constant_probability = float(y_train.mean())
        print(
            "Training window contains one target class only; using a constant-probability baseline model.",
            flush=True,
        )
        classifier = ConstantProbabilityClassifier(probability=constant_probability)
        model_type = "constant_probability_baseline"
    else:
        classifier = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)
        model_type = "balanced_logistic_regression"

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("classifier", classifier),
        ]
    )
    model.fit(X_train, y_train)
    y_score = model.predict_proba(X_test)[:, 1]
    y_pred = (y_score >= 0.5).astype(int)
    trained_at = datetime.now(timezone.utc).isoformat()
    average_precision = _safe_metric(average_precision_score, y_test, y_score)
    roc_auc = _safe_metric(roc_auc_score, y_test, y_score)
    precision_at_top_5_percent = _precision_at_top_fraction(y_test, y_score, fraction=0.05)

    metrics = {
        "duid": duid.upper(),
        "days": int(days),
        "horizon_hours": int(horizon_hours),
        "target_column": target_column,
        "connection_point_id": connection_point,
        "relevant_genconid_count": len(relevant),
        "model_type": model_type,
        "total_samples": int(len(frame)),
        "train_samples": int(len(train_df)),
        "test_samples": int(len(test_df)),
        "positive_rate": float(frame[target_column].mean()),
        "train_positive_rate": float(y_train.mean()),
        "test_positive_rate": float(y_test.mean()),
        "average_precision": average_precision,
        "roc_auc": roc_auc,
        "precision_at_top_5_percent": precision_at_top_5_percent,
        "features": feature_cols,
        "trained_at": trained_at,
        "dropped_incomplete_horizon_rows": dropped_incomplete_horizon_rows,
        "coordinate_source": coordinate_metadata,
        "rows": int(len(frame)),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "test_average_precision": average_precision,
        "test_roc_auc": roc_auc,
        "classification_report": classification_report(y_test, y_pred, zero_division=0, output_dict=True),
    }
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    # Keep only relevant planned-data rows in the model bundle to keep it lightweight.
    gencondata_u = gencondata.copy()
    gencondata_u.columns = [str(c).strip().upper() for c in gencondata_u.columns]
    genconset_u = genconset.copy()
    genconset_u.columns = [str(c).strip().upper() for c in genconset_u.columns]
    genconsetinvoke_u = genconsetinvoke.copy()
    genconsetinvoke_u.columns = [str(c).strip().upper() for c in genconsetinvoke_u.columns]

    relevant_gdata = gencondata_u[
        gencondata_u.get("GENCONID", pd.Series(index=gencondata_u.index, dtype=str))
        .astype(str)
        .str.upper()
        .isin(relevant)
    ].copy()
    relevant_gset = genconset_u[
        genconset_u.get("GENCONID", pd.Series(index=genconset_u.index, dtype=str))
        .astype(str)
        .str.upper()
        .isin(relevant)
    ].copy()
    relevant_sets = set()
    if not relevant_gset.empty and "GENCONSETID" in relevant_gset.columns:
        relevant_sets = set(relevant_gset["GENCONSETID"].astype(str).str.upper())
    if relevant_sets and "GENCONSETID" in genconsetinvoke_u.columns:
        relevant_invocations = genconsetinvoke_u[
            genconsetinvoke_u["GENCONSETID"].astype(str).str.upper().isin(relevant_sets)
        ].copy()
    else:
        relevant_invocations = pd.DataFrame()

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
        "planned_gencondata": relevant_gdata,
        "planned_genconset": relevant_gset,
        "planned_genconsetinvoke": relevant_invocations,
        "metrics": metrics,
        "trained_at_utc": trained_at,
        "horizon_hours": int(horizon_hours),
        "target_column": target_column,
        "metrics_out": str(metrics_path),
        "bind_threshold": bind_threshold,
        "include_dispatch_features": include_dispatch_features,
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
    parser.add_argument("--include-dispatch-features", action="store_true")
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
        include_dispatch_features=args.include_dispatch_features,
        no_weather=args.no_weather,
    )


if __name__ == "__main__":
    main()
