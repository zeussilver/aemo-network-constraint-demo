# Phase 2 Horizon Training Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase2.md`
Source Codex Lead prompt: extracted

## 1. Current Repository State

- The repository is a Git worktree on `main`; before Phase 2 edits, only `doc/phase2.md` is untracked.
- Phase 1.5 selected `CATHROCK`, `60` days, `6` hour horizon, connection point `SCRK`, with `228` positive horizon intervals and `0.013194` positive rate.
- `src/aemo_constraint_demo/scan.py` already contains future-only horizon-label helpers: `add_horizon_labels` and `horizon_label_column`.
- `src/aemo_constraint_demo/train.py` currently trains on `target_constrained`, requires `--lat` and `--lon`, does not accept `--horizon-hours`, and stores metrics only inside the joblib bundle.
- `app.py` already accepts `horizon_hours` and `model_path`, delegating response construction to `src/aemo_constraint_demo/forecast.py`.
- Existing tests cover current-label training, scan horizon labels, FastAPI forecast routing, and forecast bundle behavior.

## 2. Scope

- Add `--horizon-hours` to training and train against `target_constrained_next_6h` for the Phase 2 command.
- Reuse the existing future-only horizon-label helper and exclude incomplete tail rows from horizon training.
- Add vetted coordinate handling through `data/duid_coordinates.csv` and allow `--lat`/`--lon` overrides.
- Train and save a non-baseline model for `CATHROCK`, `60` days, `6` hour horizon unless the data unexpectedly contains one training class.
- Emit `artifacts/cathrock_6h.joblib` and `artifacts/cathrock_6h_metrics.json`.
- Polish forecast output with horizon metadata, model metadata, risk levels, and planned/relevant constraint information.
- Generate `artifacts/example_cathrock_6h_response.json`.
- Update README and offline tests.
- Create `chatref/phase2/` containing the required handoff files plus a Phase 2 execution report.

## 3. Out of Scope

- No UI changes.
- No new model family beyond the existing small sklearn classifier path unless needed for correctness.
- No large raw AEMO downloads committed as final deliverables.
- No future observed constraints or future dispatch outcomes as features.
- No fabricated coordinates or metrics.

## 4. Files to Create or Update

- Create `plan/phase2_execution_plan.md`
- Create `data/duid_coordinates.csv`
- Update `src/aemo_constraint_demo/train.py`
- Update `src/aemo_constraint_demo/forecast.py`
- Update `app.py` only if the route needs response-shape support beyond `forecast.py`
- Update `tests/test_train.py`
- Update `tests/test_api.py`
- Update `tests/test_features.py` or `tests/test_forecast.py` if needed
- Update `README.md`
- Create `artifacts/cathrock_6h.joblib`
- Create `artifacts/cathrock_6h_metrics.json`
- Create `artifacts/example_cathrock_6h_response.json`
- Create `chatref/phase2/phase2_horizon_training_report.md`
- Copy required handoff files into `chatref/phase2/`

## 5. Implementation Order

1. Add coordinate lookup and horizon target selection to `train.py`.
2. Persist required metrics JSON with Phase 2 field names and keep backward-compatible metric aliases where practical.
3. Add model bundle metadata for `horizon_hours`, `target_column`, and coordinate source.
4. Add forecast risk levels and explicit model metadata/relevant constraint fields.
5. Add or adjust tests for CLI parsing, horizon labels, leakage-safe feature selection, metrics JSON, API horizon response, and mocked model artifacts.
6. Update README with Phase 2 target, training command, API command, horizon-label definition, metrics summary, and limitations.
7. Run focused tests, then full tests.
8. Run the CATHROCK training command and inspect metrics/model type.
9. Generate the example API response using `forecast_from_bundle`.
10. Create the Phase 2 handoff report and copy required files to `chatref/phase2/`.
11. Run Git status/diff and commit the Phase 2 work if validation succeeds.

## 6. Validation Commands

```bash
python -m pytest tests/test_train.py tests/test_api.py tests/test_features.py tests/test_forecast.py -q
```

```bash
python -m pytest -q
```

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib
```

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

## 7. Expected Outputs

- `artifacts/cathrock_6h.joblib`
- `artifacts/cathrock_6h_metrics.json`
- `artifacts/example_cathrock_6h_response.json`
- `data/duid_coordinates.csv`
- Updated README/test/code files
- `chatref/phase2/phase2_horizon_training_report.md`
- Required handoff copies under `chatref/phase2/`

## 8. Acceptance Criteria

- Training CLI accepts `--horizon-hours 6`.
- Training target is `target_constrained_next_6h`, not the old current-interval target.
- Horizon labels are future-only and current interval is excluded.
- Feature columns exclude target columns and future-label columns.
- Metrics JSON includes all required Phase 2 fields, with undefined metrics recorded as `null`.
- CATHROCK model artifact exists and records whether the model is baseline or non-baseline.
- API response includes `horizon_hours`, model metadata, probabilities, risk levels, relevant constraints, and planned constraints.
- `python -m pytest -q` passes.
- Required handoff files are collected under `chatref/phase2/`.
- Git status clearly shows Phase 2 changes and a commit is created after successful validation.

## 9. Final Report Format

- Summary
- Files changed
- Tests
- Training result
- API result
- Whether the model is non-baseline
- Remaining issues
- Next command
