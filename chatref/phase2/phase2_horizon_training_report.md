# Phase 2 Horizon Training Report

Timestamp: `2026-05-19 22:50:02 AEST`
Project directory: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`

## Objective

Implement Phase 2: train a horizon-based ML model for `CATHROCK`, `60` days, `6` hour horizon, and expose it through the API demo.

## Files Changed

- `plan/phase2_execution_plan.md`
- `data/duid_coordinates.csv`
- `src/aemo_constraint_demo/train.py`
- `src/aemo_constraint_demo/forecast.py`
- `app.py`
- `tests/test_train.py`
- `tests/test_api.py`
- `tests/test_features.py`
- `tests/test_forecast.py`
- `README.md`
- `artifacts/cathrock_6h_metrics.json`
- `artifacts/example_cathrock_6h_response.json`
- `chatref/phase2/phase2_horizon_training_report.md`

Generated but ignored by Git in `artifacts/`:

- `artifacts/cathrock_6h.joblib`

## Commands Run

```bash
python -m pytest tests/test_train.py tests/test_api.py tests/test_features.py tests/test_forecast.py -q
```

Result:

```text
17 passed in 1.55s
```

```bash
python -m pytest -q
```

Result:

```text
40 passed in 1.03s
```

Training command:

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib
```

API example generation:

```bash
python - <<'PY'
import json
from pathlib import Path
from src.aemo_constraint_demo.forecast import forecast_from_bundle

response = forecast_from_bundle(
    "artifacts/cathrock_6h.joblib",
    duid="CATHROCK",
    horizon_hours=6,
    start="2026-05-20 00:00:00",
)
Path("artifacts/example_cathrock_6h_response.json").write_text(
    json.dumps(response, indent=2, default=str),
    encoding="utf-8",
)
PY
```

API route smoke test:

```bash
python - <<'PY'
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
response = client.get(
    "/forecast/CATHROCK",
    params={
        "horizon_hours": 6,
        "model_path": "artifacts/cathrock_6h.joblib",
        "start": "2026-05-20 00:00:00",
    },
)
print(response.status_code)
payload = response.json()
print(payload["duid"], payload["horizon_hours"], payload["model"]["model_type"], len(payload["forecast"]), payload["forecast"][0]["risk_level"])
PY
```

Result:

```text
200
CATHROCK 6 balanced_logistic_regression 72 very_low
```

Equivalent curl:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib&start=2026-05-20%2000:00:00"
```

## Training Result

- DUID: `CATHROCK`
- Connection point: `SCRK`
- Days: `60`
- Horizon: `6` hours
- Target column: `target_constrained_next_6h`
- Relevant GENCONIDs: `103`
- Model artifact: `artifacts/cathrock_6h.joblib`
- Metrics artifact: `artifacts/cathrock_6h_metrics.json`
- Model type: `balanced_logistic_regression`
- Non-baseline model: `yes`

Metrics:

```json
{
  "total_samples": 17208,
  "train_samples": 13766,
  "test_samples": 3442,
  "positive_rate": 0.013249651324965132,
  "train_positive_rate": 0.016562545401714368,
  "test_positive_rate": 0.0,
  "average_precision": null,
  "roc_auc": null,
  "precision_at_top_5_percent": 0.0,
  "dropped_incomplete_horizon_rows": 72
}
```

The chronological test split has no positive horizon events, so `average_precision` and `roc_auc` are recorded as `null`.

## API Result

Example response path:

- `artifacts/example_cathrock_6h_response.json`

Observed example summary:

```json
{
  "duid": "CATHROCK",
  "horizon_hours": 6,
  "forecast_rows": 72,
  "model_type": "balanced_logistic_regression",
  "first_probability": 0.0124,
  "first_risk_level": "very_low"
}
```

The response includes:

- `horizon_hours`
- model metadata
- full relevant GENCONID list and count
- per-interval probabilities
- per-interval risk levels
- planned relevant constraint list where available

## Handoff Folder

Collected files under `chatref/phase2/`:

- `phase2_horizon_training_report.md`
- `cathrock_6h.joblib`
- `cathrock_6h_metrics.json`
- `example_cathrock_6h_response.json`
- `README.md`
- `app.py`
- `train.py`
- `forecast.py`
- `scan.py`
- `duid_coordinates.csv`
- `duid_constraint_scan_expanded_top.csv`
- `test_train.py`
- `test_api.py`
- `test_features.py`
- `test_forecast.py`
- `test_scan.py`
- `phase2.md`
- `phase2_execution_plan.md`

## Coordinate Source

`data/duid_coordinates.csv` contains a vetted `CATHROCK` row. The DUID/name mapping is supported by AEMO ELI material, and coordinates are sourced from Global Energy Monitor and The Wind Power:

- https://www.gem.wiki/Cathedral_Rocks_wind_farm
- https://www.thewindpower.net/windfarm_en_2676_cathedral-rocks.php

## Issues Encountered

- Logistic regression emitted sklearn numerical runtime warnings during fitting, but training completed and produced a usable probability model.
- The chronological held-out window has no positive horizon labels, so AP and ROC AUC are undefined for this split.
- `data/cache` is about `974M` after training and is excluded from Git/handoff packaging.

## GitHub Polish Readiness

Ready for a GitHub demo with stated limitations. The end-to-end path now works:

```text
DUID -> relevant GENCONIDs -> future-only 6h labels -> weather/time features -> ML model -> API forecast response
```

Recommended next step: add a short backtesting note or alternate validation window so the demo can show a held-out split with positive events as well as the strict chronological result.

## Acceptance Criteria Status

- `--horizon-hours` accepted by train CLI: `pass`
- Selected horizon label used: `pass`
- Future/current target columns excluded from features: `pass`
- Metrics JSON includes `horizon_hours` and required Phase 2 fields: `pass`
- API response includes `horizon_hours`: `pass`
- API can load the horizon-trained artifact: `pass`
- `python -m pytest -q` passes: `pass`
- Model is non-baseline: `pass`
- Handoff files collected under `chatref/phase2`: `pass`
