from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import price_backtest, price_ensemble_backtest as peb
from src.aemo_constraint_demo import price_model as pmodel

NEM = "+10:00"


def predictions_csv(tmp_path: Path, days: int = 21) -> Path:
    """Scored rows where one forecast is good early and another takes over later."""
    rows_per_day = 48
    stamps = pd.date_range("2026-08-01", periods=rows_per_day * days, freq="30min", tz=NEM)
    rows = len(stamps)
    frame = pd.DataFrame(
        {
            "published": stamps,
            "target": stamps,
            "horizon_min": np.tile([30, 300], rows // 2),
            "horizon_bucket": np.tile(["0-2h", "2-6h"], rows // 2),
            "regime": np.where(np.arange(rows) % 4 == 0, "volatile", "normal"),
            "fold": "2026-08",
            "actual_rrp": 100.0,
            "aemo_base": 100.0,
            "last_rrp": 100.0,
        }
    )
    offsets = {
        "scenarios": 0.0,          # always right
        "scenarios_recentred": 25.0,
        "persistence": 40.0,
        "model": 60.0,
    }
    for name, offset in offsets.items():
        for level in pmodel.QUANTILES:
            frame[f"{name}_q{int(level * 100):02d}"] = 100.0 + offset + (level - 0.5) * 10
    path = tmp_path / "backtest_predictions_TEST1.csv.gz"
    with gzip.open(path, "wt", newline="") as handle:
        frame.to_csv(handle, index=False)
    return path


def test_score_slices_covers_every_forecast_and_slice(tmp_path: Path) -> None:
    scored = pd.read_csv(predictions_csv(tmp_path), parse_dates=["published", "target"])
    predictions = {name: price_backtest.read_predictions(scored, name) for name in price_backtest.FORECASTS}

    report = peb.score_slices(scored, predictions)

    assert set(report["overall"]) == set(price_backtest.FORECASTS)
    assert set(report["by_horizon"]) == {"0-2h", "2-6h"}
    assert set(report["by_regime"]) == {"normal", "volatile"}
    totals = sum(report["by_regime"][regime]["model"]["n"] for regime in report["by_regime"])
    assert totals == len(scored)


def test_the_ensemble_run_writes_a_report_that_names_the_best_fixed_forecast(
    tmp_path: Path, monkeypatch
) -> None:
    path = predictions_csv(tmp_path)
    out = tmp_path / "ensemble.json"

    exit_code = peb.main(["--region", "TEST1", "--predictions", str(path), "--out", str(out)])

    assert exit_code == 0
    report = json.loads(out.read_text())
    assert report["best_fixed_forecast"] == "scenarios"  # the one that is always right
    # every combination is scored against that same baseline
    assert set(report["crps_vs_best_fixed"]) >= set(peb.COMBINATIONS)
    assert report["crps_vs_best_fixed"]["scenarios"] == 0.0


def test_adaptive_weights_learn_to_favour_the_accurate_forecast(tmp_path: Path) -> None:
    path = predictions_csv(tmp_path)
    out = tmp_path / "ensemble.json"

    peb.main(["--region", "TEST1", "--predictions", str(path), "--out", str(out)])
    report = json.loads(out.read_text())

    weights = report["weights"]["ensemble_weight"]["overall"]
    assert weights["scenarios"] == max(weights.values())
    selected = report["weights"]["ensemble_select"]["overall"]
    assert selected["scenarios"] > 0.9  # after the warm-up it picks the leader every time


def test_each_combination_weights_only_the_base_forecasts(tmp_path: Path) -> None:
    """An ensemble must not become a member of the next one, or it is counted twice."""
    path = predictions_csv(tmp_path)
    out = tmp_path / "ensemble.json"

    peb.main(["--region", "TEST1", "--predictions", str(path), "--out", str(out)])
    report = json.loads(out.read_text())

    for name in peb.COMBINATIONS:
        assert set(report["weights"][name]["overall"]) == set(price_backtest.FORECASTS)


def test_an_adaptive_ensemble_lands_between_its_members(tmp_path: Path) -> None:
    path = predictions_csv(tmp_path)
    out = tmp_path / "ensemble.json"

    peb.main(["--region", "TEST1", "--predictions", str(path), "--out", str(out)])
    report = json.loads(out.read_text())
    overall = report["overall"]

    assert overall["ensemble_weight"]["crps"] < overall["model"]["crps"]
    assert overall["ensemble_weight"]["crps"] > overall["scenarios"]["crps"]
    # selection is the sharper rule and should get closer to the leader than averaging
    assert overall["ensemble_select"]["crps"] < overall["ensemble_weight"]["crps"]
