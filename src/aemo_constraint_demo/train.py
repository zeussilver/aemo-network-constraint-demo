"""Train a small DUID network-constraint forecaster."""
from __future__ import annotations

import argparse
import json
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
from .weather import fetch_open_meteo_history, merge_weather_to_intervals


def _safe_metric(func, y_true, y_score):
    try:
        if pd.Series(y_true).nunique(dropna=True) < 2:
            return None
        return float(func(y_true, y_score))
    except Exception:  # noqa: BLE001 - metric undefined when test has one class
        return None


def train_demo_model(
    duid: str,
    latitude: float,
    longitude: float,
    days: int = 21,
    cache_dir: str | Path = "data/cache",
    model_out: str | Path = "artifacts/model.joblib",
    static_year: int | None = None,
    static_month: int | None = None,
    bind_threshold: float = 0.0,
    include_dispatch_features: bool = False,
    no_weather: bool = False,
) -> dict:
    cache_dir = Path(cache_dir)
    model_out = Path(model_out)
    model_out.parent.mkdir(parents=True, exist_ok=True)

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
    frame = frame.dropna(subset=["target_constrained", "SETTLEMENTDATE"])
    frame = frame.sort_values("SETTLEMENTDATE").reset_index(drop=True)

    split = max(1, int(len(frame) * 0.8))
    train_df = frame.iloc[:split].copy()
    test_df = frame.iloc[split:].copy()
    if test_df.empty:
        raise ValueError("Not enough rows for a train/test split. Increase --days.")

    X_train, y_train = train_df[feature_cols], train_df["target_constrained"].astype(int)
    X_test, y_test = test_df[feature_cols], test_df["target_constrained"].astype(int)

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

    metrics = {
        "rows": int(len(frame)),
        "positive_rate": float(frame["target_constrained"].mean()),
        "train_positive_rate": float(y_train.mean()),
        "test_positive_rate": float(y_test.mean()),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "model_type": model_type,
        "test_average_precision": _safe_metric(average_precision_score, y_test, y_score),
        "test_roc_auc": _safe_metric(roc_auc_score, y_test, y_score),
        "classification_report": classification_report(y_test, y_pred, zero_division=0, output_dict=True),
    }

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
        "relevant_genconids": relevant,
        "lhs_rows": lhs_rows,
        "planned_gencondata": relevant_gdata,
        "planned_genconset": relevant_gset,
        "planned_genconsetinvoke": relevant_invocations,
        "metrics": metrics,
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "bind_threshold": bind_threshold,
        "include_dispatch_features": include_dispatch_features,
        "no_weather": no_weather,
    }
    joblib.dump(bundle, model_out)

    result = {
        "model_out": str(model_out),
        "duid": duid.upper(),
        "connection_point": connection_point,
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
    parser.add_argument("--lat", type=float, required=True, help="Latitude for weather features")
    parser.add_argument("--lon", type=float, required=True, help="Longitude for weather features")
    parser.add_argument("--days", type=int, default=21, help="Number of recent Next Day Dispatch daily files")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--model-out", default="artifacts/model.joblib")
    parser.add_argument("--static-year", type=int, default=None, help="Optional MMSDM standing-data year")
    parser.add_argument("--static-month", type=int, default=None, help="Optional MMSDM standing-data month")
    parser.add_argument("--bind-threshold", type=float, default=0.0)
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
        static_year=args.static_year,
        static_month=args.static_month,
        bind_threshold=args.bind_threshold,
        include_dispatch_features=args.include_dispatch_features,
        no_weather=args.no_weather,
    )


if __name__ == "__main__":
    main()
