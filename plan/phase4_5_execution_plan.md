# Phase 4.5 Offline-Safe API Demo Patch Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase4.5.md`
Source Codex Lead prompt: extracted from the fenced `给 Codex Lead 的最终修补提示词` block in `doc/phase4.5.md`

## 1. Current Repository State

- The repository is a Git worktree.
- Pre-existing uncommitted state before Phase 4.5 edits:
  - modified: `.gitignore`, `README.md`, `artifacts/example_cathrock_6h_response.json`, `notebooks/aemo_constraint_forecast_demo.ipynb`, `requirements.txt`, `src/aemo_constraint_demo/forecast.py`
  - deleted: `chatref/phase0/codex_execute_report.md`, `phase1.md`, `phase1_5.md`
  - untracked: `application/`, `artifacts/cathrock_6h_best_model.joblib`, `chatref/codex_execute_report.md`, `chatref/phase4_final_polish_report.md`, `doc/phase3.md`, `doc/phase4.5.md`, `doc/phase4.md`, `plan/phase4_final_polish.md`
- `src/aemo_constraint_demo/forecast.py` currently calls `fetch_open_meteo_forecast` directly when `bundle["no_weather"]` is false. If that request raises, the forecast call raises.
- `app.py` currently exposes `horizon_hours`, `model_path`, and `start`, but not `use_live_weather`.
- `tests/test_forecast.py` already covers interval grid creation, planned constraints, forecast rows, horizon inference, mismatched horizons, and DUID mismatch.
- `tests/test_api.py` already covers health, forecast rows, horizon inference, mismatched horizons, missing model, and DUID mismatch.
- `README.md` has online query examples but no offline-safe command or fallback explanation.
- `artifacts/example_cathrock_6h_response.json` exists for `CATHROCK` 6h, but does not yet include `weather_source` or `weather_warning`.
- `chatref/` is the repo's handoff/report convention.

## 2. Scope

- Add `use_live_weather: bool = True` to `forecast_from_bundle`.
- Avoid Open-Meteo calls when `use_live_weather=False`.
- Catch live weather failures when `use_live_weather=True` and fall back to imputed weather features.
- Include top-level response metadata:
  - `weather_source`
  - `weather_warning`
- Add `use_live_weather` to the FastAPI route query parameters.
- Extend focused forecast and API tests for offline-safe behavior and fallback metadata.
- Refresh `artifacts/example_cathrock_6h_response.json` using the best-model bundle, deterministic start timestamp, and `use_live_weather=False`.
- Update README query-service instructions with online and offline-safe commands plus a short fallback explanation.
- Create `chatref/phase4_5_offline_api_report.md`.

## 3. Out of Scope

- No model retraining.
- No new model features.
- No UI.
- No database.
- No production P5MIN/Predispatch integration.
- No fabricated metrics or quality claims beyond validation evidence.
- No cleanup of pre-existing unrelated Git deletions unless explicitly requested.
- No broad refactors outside API fallback robustness.

## 4. Files to Create or Update

- Create `plan/phase4_5_execution_plan.md`
- Update `src/aemo_constraint_demo/forecast.py`
- Update `app.py`
- Update `tests/test_forecast.py`
- Update `tests/test_api.py`
- Update `README.md`
- Refresh `artifacts/example_cathrock_6h_response.json`
- Create `chatref/phase4_5_offline_api_report.md`

## 5. Implementation Order

1. Add fallback metadata and `use_live_weather` handling to `forecast_from_bundle`.
2. Add the `use_live_weather` query parameter to the FastAPI route and pass it through.
3. Add forecast-helper tests for disabled live weather and mocked Open-Meteo failure fallback.
4. Add API tests for `use_live_weather=false`, response metadata, horizon inference, and horizon mismatch preservation.
5. Update README query examples and fallback wording.
6. Regenerate `artifacts/example_cathrock_6h_response.json` from `artifacts/cathrock_6h_best_model.joblib` using `start="2026-05-20 00:00:00"` and `use_live_weather=False`.
7. Run focused tests, then the full suite.
8. Inspect the refreshed example response for required metadata and forecast rows.
9. Write the Phase 4.5 report.
10. Review Git status and diff summary for handoff.

## 6. Validation Commands

```bash
python -m pytest tests/test_forecast.py tests/test_api.py -q
```

```bash
python -m pytest -q
```

```bash
python - <<'PY'
from src.aemo_constraint_demo.forecast import forecast_from_bundle

payload = forecast_from_bundle(
    "artifacts/cathrock_6h_best_model.joblib",
    duid="CATHROCK",
    start="2026-05-20 00:00:00",
    use_live_weather=False,
)
print(payload["duid"], payload["horizon_hours"], payload["model"]["model_type"], payload["model_metrics"]["feature_set"])
print(payload["weather_source"], payload["weather_warning"], len(payload["forecast"]))
PY
```

```bash
python - <<'PY'
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
response = client.get(
    "/forecast/CATHROCK",
    params={
        "model_path": "artifacts/cathrock_6h_best_model.joblib",
        "start": "2026-05-20 00:00:00",
        "use_live_weather": "false",
    },
)
print(response.status_code)
payload = response.json()
print(payload["duid"], payload["horizon_hours"], payload["model"]["model_type"], payload["weather_source"], len(payload["forecast"]))
PY
```

```bash
git status --short
git diff --stat
```

## 7. Expected Outputs

- `GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib` still works online when Open-Meteo is available.
- `GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false` works without an Open-Meteo call.
- If Open-Meteo fails, the API still returns a forecast with `weather_source == "fallback_imputed"`.
- Successful live weather calls return `weather_source == "live_open_meteo"` and `weather_warning == null`.
- Fallback calls return `weather_warning == "Open-Meteo forecast unavailable; weather features were imputed."`
- `artifacts/example_cathrock_6h_response.json` includes `horizon_hours == 6`, `model.model_type == "logistic_regression"`, `model_metrics.feature_set == "time_weather_dispatch_lag"`, `weather_source == "fallback_imputed"`, and more than zero forecast rows.
- `chatref/phase4_5_offline_api_report.md` exists and summarizes validation evidence.

## 8. Acceptance Criteria

- `forecast_from_bundle(use_live_weather=False)` does not call Open-Meteo and returns forecast rows.
- `forecast_from_bundle` catches mocked Open-Meteo failure and returns fallback metadata.
- API route accepts `use_live_weather=false`.
- API response includes `weather_source`.
- Omitted `horizon_hours` still infers the 6h model horizon.
- Explicit mismatched `horizon_hours` still returns `400`.
- `python -m pytest -q` passes.
- README includes online and offline-safe forecast commands.
- Example response and Phase 4.5 report are refreshed.

## 9. Final Report Format

- Summary
- Files changed
- Tests
- API fallback result
- README/example response status
- Data or implementation issues found
- Acceptance criteria status
- Ready for GitHub
- Next git commands
