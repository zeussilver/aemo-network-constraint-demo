# Phase 2.5 Evaluation Hardening Report

Timestamp: `2026-05-20T00:19:32+10:00`

## Summary

Phase 2.5 added event-window backtesting, API horizon inference, refreshed API examples, and README/notebook polish for the `CATHROCK` 60-day 6h horizon demo.

The demo remains intentionally honest: the main chronological holdout has no positive labels, so chronological AP and ROC AUC stay `null`. The new event-window backtest is a transparent supplement, not a production-accuracy claim.

## Files Changed

- `plan/phase2_5_execution_plan.md`
- `scripts/backtest_horizon_model.py`
- `src/aemo_constraint_demo/forecast.py`
- `app.py`
- `tests/test_backtest.py`
- `tests/test_api.py`
- `tests/test_forecast.py`
- `tests/test_notebook_readme.py`
- `README.md`
- `notebooks/aemo_constraint_forecast_demo.ipynb`
- `artifacts/cathrock_6h_backtest_summary.json`
- `artifacts/cathrock_6h_backtest_folds.csv`
- `artifacts/example_cathrock_6h_response.json`
- `artifacts/example_cathrock_6h_backtest_response.json`
- `chatref/phase2_5_evaluation_report.md`

## Commands Run

```bash
python -m pytest tests/test_backtest.py tests/test_api.py tests/test_forecast.py -q
```

```bash
python -m pytest tests/test_backtest.py tests/test_api.py tests/test_forecast.py tests/test_train.py tests/test_notebook_readme.py -q
```

```bash
python scripts/backtest_horizon_model.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib \
  --out artifacts/cathrock_6h_backtest_summary.json \
  --folds-out artifacts/cathrock_6h_backtest_folds.csv \
  --max-folds 5
```

```bash
python -m pytest -q
```

API smoke command:

```bash
python - <<'PY'
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
model_path = 'artifacts/cathrock_6h.joblib'
ok = client.get('/forecast/CATHROCK', params={'model_path': model_path, 'start': '2026-05-20 08:00:00'})
bad = client.get('/forecast/CATHROCK', params={'model_path': model_path, 'horizon_hours': 24, 'start': '2026-05-20 08:00:00'})
print(ok.status_code, ok.json()['horizon_hours'], len(ok.json()['forecast']))
print(bad.status_code, bad.json()['detail'])
PY
```

## Test Result

```text
50 passed in 1.10s
```

## Backtest Result

Output paths:

```text
artifacts/cathrock_6h_backtest_summary.json
artifacts/cathrock_6h_backtest_folds.csv
```

Summary:

```text
evaluated_folds: 2
skipped_folds: 1
mean_average_precision: 0.4292536031939571
mean_roc_auc: 0.5348134028244604
mean_precision_at_top_5_percent: 0.43333333333333335
mean_lift_at_top_5_percent: 0.8856345408976989
```

Skipped fold reason:

```text
prior_training_has_one_target_class
```

## API Result

Horizon inference works.

```text
GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h.joblib
status: 200
horizon_hours: 6
forecast_rows: 72
first_probability: 0.0043
```

Mismatch handling works.

```text
GET /forecast/CATHROCK?horizon_hours=24&model_path=artifacts/cathrock_6h.joblib
status: 400
detail: Requested horizon_hours=24 does not match model bundle horizon_hours=6.
```

Example outputs:

```text
artifacts/example_cathrock_6h_response.json
artifacts/example_cathrock_6h_backtest_response.json
```

The historical example is labelled `historical_backtest_demo` and `not_real_future_forecast: true`.

## README and Notebook

README updated: yes.

Notebook updated: yes.

The notebook now follows the Phase 2.5 story:

- target discovery summary;
- why `CATHROCK` was selected;
- `target_constrained_next_6h` definition;
- training command and metrics;
- event-window backtest summary;
- API query examples;
- limitations.

## Remaining Limitations

- The chronological holdout still has no positive `CATHROCK` 6h horizon labels, so chronological AP/AUC remain `null`.
- Event-window folds are sparse and partly overlapping.
- Mean top-5% lift is below 1.0, so the model should not be described as accurate.
- Features remain a lightweight demo set: time and weather only by default.
- Production work still needs longer history, P5MIN/Predispatch inputs, outage context, constraint taxonomy, calibration, and monitoring.

## Final GitHub Polish Readiness

Ready for Phase 3 GitHub polish: yes, with the above sparse-event limitations kept explicit.
