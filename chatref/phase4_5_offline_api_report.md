# Phase 4.5 Offline-Safe API Demo Patch Report

Timestamp: `2026-05-20 15:07:17 AEST`

## Summary

Phase 4.5 is complete. The forecast API now supports `use_live_weather=false`, skips Open-Meteo when that flag is disabled, and falls back to imputed weather features if the live Open-Meteo request raises. The API response includes `weather_source` and `weather_warning`, README now documents online and offline-safe commands, and the CATHROCK 6h example response was refreshed from the packaged best-model bundle.

## Files Changed

Phase 4.5-owned changes:

- `app.py`
  - Added `use_live_weather: bool = Query(default=True)`.
  - Passes `use_live_weather` through to `forecast_from_bundle`.
- `src/aemo_constraint_demo/forecast.py`
  - Added `use_live_weather` to `forecast_from_bundle`.
  - Added Open-Meteo fallback handling and response metadata.
  - Leaves missing weather/dispatch-lag columns as `NA` for the model pipeline imputer.
- `tests/test_forecast.py`
  - Added direct helper coverage for disabled live weather and mocked Open-Meteo failure fallback.
- `tests/test_api.py`
  - Added route coverage for `use_live_weather=false` and mocked Open-Meteo failure returning `200`.
  - Verified `weather_source` and `weather_warning` in API payloads.
- `README.md`
  - Added online and offline-safe forecast commands.
  - Explained live-weather fallback and imputed demo behavior.
- `artifacts/example_cathrock_6h_response.json`
  - Refreshed using `artifacts/cathrock_6h_best_model.joblib`, `duid=CATHROCK`, `start="2026-05-20 00:00:00"`, and `use_live_weather=false`.
- `plan/phase4_5_execution_plan.md`
  - Added the required execution plan.
- `chatref/phase4_5_offline_api_report.md`
  - Added this handoff report.

Pre-existing unrelated/unresolved Git state still visible:

- deleted: `chatref/phase0/codex_execute_report.md`, `phase1.md`, `phase1_5.md`
- untracked: `chatref/codex_execute_report.md`, `doc/phase3.md`, `doc/phase4.md`
- Phase 4 files from the previous polish pass remain modified/untracked.

## Tests

Focused Phase 4.5 suite:

```text
python -m pytest tests/test_forecast.py tests/test_api.py -q
16 passed in 0.33s
```

Full suite:

```text
python -m pytest -q
60 passed, 2 warnings in 1.62s
```

The two warnings are the existing sklearn imputer warnings from `tests/test_train.py` synthetic data with no observed values in `totalcleared_lag_6h`.

Hygiene check:

```text
git diff --check
passed
```

## API Fallback Result

Offline-safe helper smoke:

```text
CATHROCK 6 logistic_regression time_weather_dispatch_lag
fallback_imputed Open-Meteo forecast unavailable; weather features were imputed. 72
```

Offline-safe API smoke:

```text
status_code: 200
payload: CATHROCK 6 logistic_regression fallback_imputed 72
```

Mismatched-horizon API smoke:

```text
status_code: 400
detail: Requested horizon_hours=24 does not match model bundle horizon_hours=6.
```

Open-Meteo failure fallback was tested with mocked exceptions in both `forecast_from_bundle` and the FastAPI route.

## README / Example Response Status

README updated: yes.

Online command:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib"
```

Offline-safe command:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false"
```

Example response refreshed: yes.

Refreshed example check:

```text
duid: CATHROCK
horizon_hours: 6
model_type: logistic_regression
feature_set: time_weather_dispatch_lag
weather_source: fallback_imputed
weather_warning: Open-Meteo forecast unavailable; weather features were imputed.
forecast_rows: 72
first_interval_start: 2026-05-20T00:00:00
```

## Remaining Limitations

- Offline-safe mode imputes missing query-time weather and dispatch-lag features; it is for demo reproducibility, not production-quality forecasts.
- The model evidence remains sparse-fold evidence, not production accuracy.
- The planned-constraint output remains a lightweight `GENCONSETINVOKE` proxy, not a full P5MIN/Predispatch forecast-solution integration.
- Open-Meteo live success was not called as part of validation to keep the validation suite offline and deterministic.

## Acceptance Criteria Status

- `forecast_from_bundle(use_live_weather=False)` does not call Open-Meteo and returns forecast rows: pass.
- `forecast_from_bundle` catches mocked Open-Meteo failure and returns `fallback_imputed`: pass.
- API route accepts `use_live_weather=false`: pass.
- API response includes `weather_source`: pass.
- Omitted `horizon_hours` still infers the 6h bundle horizon: pass.
- Explicit mismatched `horizon_hours` still returns `400`: pass.
- README online/offline commands added: pass.
- Example response refreshed: pass.
- Full tests pass: pass.

## Ready For GitHub

Ready for GitHub: yes for the Phase 4.5 scope, after staging only the intended project files and resolving or intentionally preserving the pre-existing deleted files. Do not use `git add .` until the unrelated deleted files are handled.

## Next Git Commands

If the pre-existing deletions were accidental, restore them first:

```bash
git restore -- phase1.md phase1_5.md chatref/phase0/codex_execute_report.md
```

Then stage the intended files explicitly:

```bash
git add \
  .gitignore \
  README.md \
  requirements.txt \
  app.py \
  src/aemo_constraint_demo/forecast.py \
  tests/test_api.py \
  tests/test_forecast.py \
  notebooks/aemo_constraint_forecast_demo.ipynb \
  artifacts/example_cathrock_6h_response.json \
  artifacts/cathrock_6h_best_model.joblib \
  application/project_summary.md \
  application/resume_bullets.md \
  application/interview_talking_points.md \
  plan/phase4_final_polish.md \
  plan/phase4_5_execution_plan.md \
  chatref/phase4_final_polish_report.md \
  chatref/phase4_5_offline_api_report.md
```

Review and commit:

```bash
git status
git diff --cached --stat
git commit -m "Polish AEMO constraint forecasting demo for application"
```
