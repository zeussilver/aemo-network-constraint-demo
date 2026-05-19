# Phase 2.5 Evaluation Hardening Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase2.5.md`
Source Codex Lead prompt: extracted

## 1. Current Repository State

- The repository is a Git worktree on `main`; before Phase 2.5 edits it is ahead of `origin/main` by 4 commits and `doc/phase2.5.md` is untracked.
- Phase 2 produced `artifacts/cathrock_6h.joblib`, `artifacts/cathrock_6h_metrics.json`, and `artifacts/example_cathrock_6h_response.json`.
- Existing Phase 2 metrics show a non-baseline `balanced_logistic_regression` for `CATHROCK`, `60` days, `6` hour horizon, with `test_positive_rate = 0.0`, `average_precision = null`, and `roc_auc = null`.
- `src/aemo_constraint_demo/train.py` already has reusable model, metric, horizon-label, weather, and feature-selection helpers.
- `src/aemo_constraint_demo/features.py` restricts default features to time and weather, with optional dispatch features only when requested.
- `src/aemo_constraint_demo/forecast.py` currently defaults forecast horizon to 24 hours when called directly.
- `app.py` currently defaults the FastAPI `horizon_hours` query parameter to 24.
- Tests cover training bundles, feature exclusion, forecast helpers, and FastAPI route behavior, but not event-window backtesting.
- The notebook still uses an older `OAKEY1SF` current-label walkthrough and needs to match the `CATHROCK` 6h Phase 2 story.

## 2. Scope

- Create `scripts/backtest_horizon_model.py` for event-window backtesting of the `CATHROCK` 60-day 6h horizon model.
- Use the same target, feature engineering, model pipeline, coordinate lookup, weather handling, and leakage exclusions as `train.py`.
- Train each fold only on rows strictly before the validation window and report skipped folds instead of crashing.
- Add fold-level AP, ROC AUC, precision@top5%, precision@top10%, lift@top5%, and lift@top10%.
- Write `artifacts/cathrock_6h_backtest_summary.json` and `artifacts/cathrock_6h_backtest_folds.csv`.
- Update API behavior so omitted `horizon_hours` uses the model bundle horizon, while mismatches return a clear 400 through the existing `ValueError` path.
- Generate refreshed forecast and historical backtest/demo API example JSON files.
- Update README and notebook around the final `CATHROCK` 6h workflow and honest metric limitations.
- Add focused tests for fold generation, skipped folds, zero-positive lift handling, API horizon inference/mismatch behavior, and target-column feature exclusion.
- Create `chatref/phase2_5_evaluation_report.md`.

## 3. Out of Scope

- No deep learning, UI, database, or production data platform.
- No fabricated metrics or performance claims.
- No new model family beyond the existing small sklearn path.
- No large raw AEMO data commits.
- No Phase 3 final release polish beyond the requested README/notebook/API/demo updates.

## 4. Files to Create or Update

- Create `plan/phase2_5_execution_plan.md`
- Create `scripts/backtest_horizon_model.py`
- Update `src/aemo_constraint_demo/forecast.py`
- Update `app.py`
- Create `tests/test_backtest.py`
- Update `tests/test_api.py`
- Update `tests/test_forecast.py` if direct forecast behavior needs coverage
- Update `tests/test_notebook_readme.py`
- Update `README.md`
- Update `notebooks/aemo_constraint_forecast_demo.ipynb`
- Create or refresh:
  - `artifacts/cathrock_6h_backtest_summary.json`
  - `artifacts/cathrock_6h_backtest_folds.csv`
  - `artifacts/example_cathrock_6h_response.json`
  - `artifacts/example_cathrock_6h_backtest_response.json`
  - `chatref/phase2_5_evaluation_report.md`

## 5. Implementation Order

1. Add reusable event-window backtest helpers and CLI in `scripts/backtest_horizon_model.py`.
2. Add focused offline tests for fold construction, skipped-fold handling, safe lift metrics, and feature exclusions.
3. Update `forecast_from_bundle` and the FastAPI route so omitted `horizon_hours` is inferred from the bundle and explicit mismatches are rejected.
4. Update API tests and direct forecast tests for inference and mismatch behavior.
5. Run focused tests, then full tests.
6. Run the required Phase 2.5 backtest command.
7. Generate the refreshed current-style and historical backtest/demo API JSON examples.
8. Update README and notebook, keeping the story compact and metrics honest.
9. Run final full validation and inspect artifacts for meaningful content.
10. Create the Phase 2.5 evaluation report and review git status/diff.

## 6. Validation Commands

```bash
python -m pytest tests/test_backtest.py tests/test_api.py tests/test_forecast.py tests/test_train.py tests/test_notebook_readme.py -q
```

```bash
python -m pytest -q
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
python - <<'PY'
import json
from pathlib import Path
from src.aemo_constraint_demo.forecast import forecast_from_bundle

response = forecast_from_bundle(
    "artifacts/cathrock_6h.joblib",
    duid="CATHROCK",
    start="2026-05-20 00:00:00",
)
Path("artifacts/example_cathrock_6h_response.json").write_text(
    json.dumps(response, indent=2, default=str),
    encoding="utf-8",
)
PY
```

## 7. Expected Outputs

- Event-window backtest summary JSON with evaluated/skipped fold counts and mean metrics.
- Fold-level CSV with skip reasons where applicable.
- API responses that work when `horizon_hours` is omitted for the `CATHROCK` 6h bundle.
- A historical backtest/demo example clearly labelled as non-future.
- README and notebook that explain why chronological AP/AUC are null and why event-window backtesting is used.
- Passing tests.
- `chatref/phase2_5_evaluation_report.md` with commands, metrics, artifact paths, and remaining limitations.

## 8. Acceptance Criteria

- `python -m pytest -q` passes.
- The required backtest command completes and writes non-empty summary/fold artifacts.
- Fold training uses only rows strictly before each validation window.
- Invalid folds are skipped with recorded reasons.
- Metrics handle one-class or zero-positive validation windows safely.
- Feature columns do not include `target_constrained`, `target_constrained_next_*`, or other future labels.
- `GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h.joblib` works by inferring the model horizon.
- `GET /forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib` still works.
- Explicit horizon mismatch returns a clear 400 error through the API.
- README and notebook are updated and do not claim production accuracy.
- Phase 2.5 execution report exists.

## 9. Final Report Format

- Summary
- Files changed
- Tests
- Backtest result
- API result
- README/notebook result
- Remaining issues
- Next command
