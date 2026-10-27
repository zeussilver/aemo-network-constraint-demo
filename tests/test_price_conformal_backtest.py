from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import price_conformal_backtest as pcb
from src.aemo_constraint_demo import price_model as pmodel

NEM = "+10:00"
# standard normal quantiles at the grid the lane forecasts on
NORMAL = {0.05: -1.6449, 0.1: -1.2816, 0.25: -0.6745, 0.5: 0.0, 0.75: 0.6745, 0.9: 1.2816, 0.95: 1.6449}


def predictions_csv(
    tmp_path: Path, days: int = 30, true_sd: float = 20.0, claimed_sd: float = 5.0, seed: int = 3
) -> Path:
    """Scored rows where the model is far too sure of itself and persistence is honest.

    An interval is forecast many times over the preceding day and a half, so real scored
    days hold thousands of rows; the five-minute spacing here keeps that shape, which is
    what decides how quickly a trailing window fills.
    """
    rows_per_day = 288
    stamps = pd.date_range("2026-08-01", periods=rows_per_day * days, freq="5min", tz=NEM)
    rows = len(stamps)
    rng = np.random.default_rng(seed)
    actual = 100.0 + rng.normal(0.0, true_sd, rows)
    frame = pd.DataFrame(
        {
            "published": stamps,
            "target": stamps,
            "horizon_min": 720,
            "horizon_bucket": "6-12h",
            "regime": np.where(np.arange(rows) % 4 == 0, "volatile", "normal"),
            "fold": np.where(np.arange(rows) < rows // 2, "2026-08", "2026-09"),
            "actual_rrp": actual,
            "aemo_base": actual,
            "last_rrp": actual,
        }
    )
    for level in pmodel.QUANTILES:
        column = f"q{int(level * 100):02d}"
        frame[f"model_{column}"] = 100.0 + NORMAL[level] * claimed_sd
        frame[f"persistence_{column}"] = 100.0 + NORMAL[level] * true_sd
    path = tmp_path / "backtest_predictions_TEST1.csv.gz"
    with gzip.open(path, "wt", newline="") as handle:
        frame.to_csv(handle, index=False)
    return path


def run(tmp_path: Path, *extra: str) -> dict:
    out = tmp_path / "conformal.json"
    exit_code = pcb.main(
        [
            "--region", "TEST1",
            "--predictions", str(predictions_csv(tmp_path)),
            "--out", str(out),
            "--windows", "14,28",
            "--window", "14",
            *extra,
        ]
    )
    assert exit_code == 0
    return json.loads(out.read_text())


def test_calibration_row_reports_every_interval(tmp_path: Path) -> None:
    scored = pd.read_csv(predictions_csv(tmp_path), parse_dates=["published", "target"])
    actual = scored["actual_rrp"].to_numpy(dtype=float)

    row = pcb.calibration_row(actual, {level: scored[f"model_q{int(level * 100):02d}"].to_numpy() for level in pmodel.QUANTILES})

    assert set(row) >= {"crps", "coverage_50", "coverage_80", "coverage_90", "width_80"}
    assert row["coverage_80"] < 0.4  # the model here claims a quarter of the spread it needs


def test_the_run_scores_every_window_it_was_asked_for(tmp_path: Path) -> None:
    report = run(tmp_path)

    assert set(report["windows"]) == {"14", "28"}
    assert report["window_days"] == 14
    assert report["forecast"] == "model"


def test_recalibration_widens_an_over_confident_forecast(tmp_path: Path) -> None:
    report = run(tmp_path)

    for window in report["windows"].values():
        assert window["coverage_80"] > report["baseline"]["coverage_80"]
        assert window["coverage_80"] > 0.7
        assert window["crps"] < report["baseline"]["crps"]  # widening a bad interval also scores better


def test_both_the_forecast_and_its_calibrated_twin_are_scored(tmp_path: Path) -> None:
    report = run(tmp_path)

    assert set(report["overall"]) == {"model", "model_conformal"}
    assert set(report["reliability"]) == {"model", "model_conformal"}
    assert set(report["by_fold"]) == {"2026-08", "2026-09"}
    assert set(report["by_regime"]) == {"normal", "volatile"}
    totals = sum(report["by_fold"][fold]["model"]["n"] for fold in report["by_fold"])
    assert totals == report["rows"]


def test_the_warm_up_rows_are_reported_as_uncorrected(tmp_path: Path) -> None:
    """Early rows have no settled window behind them, and the report has to admit it."""
    report = run(tmp_path)

    offsets = report["offsets"]
    assert 0 < offsets["rows_corrected"] < report["rows"]
    assert offsets["window_days"] == 14
    assert offsets["mean_offset"]["0.05"] < 0 < offsets["mean_offset"]["0.95"]


def test_a_forecast_that_is_already_honest_is_barely_moved(tmp_path: Path) -> None:
    report = run(tmp_path, "--forecast", "persistence")

    assert set(report["overall"]) == {"persistence", "persistence_conformal"}
    for window in report["windows"].values():
        assert abs(window["coverage_80"] - report["baseline"]["coverage_80"]) < 0.05
        assert abs(window["crps_vs_baseline_pct"]) < 5.0
