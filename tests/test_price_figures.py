from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("matplotlib", reason="figures are drawn locally, not in the test matrix")

from scripts import price_figures
from src.aemo_constraint_demo import price_model as pmodel

NEM = "+10:00"


def predictions_csv(tmp_path: Path) -> Path:
    stamps = pd.date_range("2026-08-01", periods=96 * 3, freq="30min", tz=NEM)
    rows = len(stamps)
    rng = np.random.default_rng(5)
    actual = 80 + 30 * np.sin(np.arange(rows) / 8) + rng.normal(0, 5, rows)
    frame = pd.DataFrame(
        {
            "published": stamps - pd.Timedelta("12h"),
            "target": stamps,
            "horizon_min": 720,
            "horizon_bucket": "6-12h",
            "regime": "normal",
            "fold": "2026-08",
            "actual_rrp": actual,
            "aemo_base": actual + rng.normal(0, 10, rows),
            "last_rrp": actual,
        }
    )
    for name in ("scenarios", "persistence", "model"):
        for level in pmodel.QUANTILES:
            frame[f"{name}_q{int(level * 100):02d}"] = actual + (level - 0.5) * 30
    path = tmp_path / "backtest_predictions_TEST1.csv.gz"
    with gzip.open(path, "wt", newline="") as handle:
        frame.to_csv(handle, index=False)
    return path


def test_every_figure_is_written(tmp_path: Path) -> None:
    path = predictions_csv(tmp_path)
    out_dir = tmp_path / "images"

    exit_code = price_figures.main(
        ["--region", "TEST1", "--predictions", str(path), "--out-dir", str(out_dir)]
    )

    assert exit_code == 0
    written = sorted(item.name for item in out_dir.glob("*.png"))
    assert written == [
        "price-calibration-TEST1.png",
        "price-crps-by-horizon-TEST1.png",
        "price-day-TEST1.png",
    ]
    assert all((out_dir / name).stat().st_size > 5000 for name in written)


def test_the_day_figure_can_be_asked_for_a_specific_day(tmp_path: Path) -> None:
    scored = pd.read_csv(predictions_csv(tmp_path), parse_dates=["published", "target"])
    out = tmp_path / "day.png"

    price_figures.day_figure(scored, out, "TEST1", horizon_min=720, day="2026-08-02")

    assert out.exists()


def test_the_day_figure_says_when_a_horizon_is_not_in_the_data(tmp_path: Path) -> None:
    scored = pd.read_csv(predictions_csv(tmp_path), parse_dates=["published", "target"])

    with pytest.raises(ValueError, match="horizon"):
        price_figures.day_figure(scored, tmp_path / "x.png", "TEST1", horizon_min=10)
