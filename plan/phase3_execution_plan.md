# Phase 3 Performance Improvement Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase3.md`
Source Codex Lead prompt: extracted

## 1. Current Repository State

- The repository is a Git worktree on `main`.
- Pre-existing uncommitted state before Phase 3 edits:
  - deleted: `chatref/phase0/codex_execute_report.md`, `phase1.md`, `phase1_5.md`
  - untracked: `chatref/codex_execute_report.md`, `doc/phase3.md`
- Phase 2/2.5 already provide a working `CATHROCK` 60-day 6h horizon demo, FastAPI route, model bundle format, event-window backtest helper, and offline tests.
- Existing training uses `balanced_logistic_regression` with time/weather features selected by `src/aemo_constraint_demo/features.py`.
- Existing Phase 2.5 backtest reports sparse-event limitations and mean event-window metrics in `artifacts/cathrock_6h_backtest_summary.json`.
- No large raw AEMO data should be committed; `data/cache/` remains out of scope.

## 2. Scope

- Add named feature sets:
  - `time_only`
  - `time_weather`
  - `time_weather_dispatch_lag`
  - `time_weather_constraint_lag`
  - `time_weather_dispatch_constraint_lag`
- Add leakage-guarded lag/rolling features using current or prior rows only.
- Add model choices:
  - `logistic_regression`
  - `random_forest`
  - `hist_gradient_boosting`
- Add `--feature-set` and `--model-type` to training while keeping current defaults compatible.
- Create `scripts/benchmark_models.py` for event-window benchmarking across feature sets and model types.
- Write benchmark CSV, best model bundle, feature importance CSV, and Phase 3 report.
- Update README with a concise Phase 3 section after benchmark execution.
- Add focused tests for feature-set safety, past-only lag construction, model selection, benchmark schema/skips, and best-bundle metadata.

## 3. Out of Scope

- No deep learning or sequence architecture rewrite.
- No LightGBM/XGBoost/CatBoost dependency.
- No full P5MIN/Predispatch integration in this phase.
- No random time-series split.
- No fabricated metrics or claims of production accuracy.
- No deletion of previous model artifacts.
- No commits of large raw AEMO cache files.

## 4. Files to Create or Update

- Create `plan/phase3_execution_plan.md`
- Update `src/aemo_constraint_demo/features.py`
- Update `src/aemo_constraint_demo/train.py`
- Update `scripts/backtest_horizon_model.py` only if needed for shared feature/model helpers
- Create `scripts/benchmark_models.py`
- Update `tests/test_features.py`
- Update `tests/test_train.py`
- Create `tests/test_benchmark_models.py`
- Update `README.md`
- Create or refresh:
  - `artifacts/performance_benchmark.csv`
  - `artifacts/cathrock_6h_best_model.joblib`
  - `artifacts/cathrock_6h_feature_importance.csv`
  - `chatref/phase3_performance_report.md`

## 5. Implementation Order

1. Add feature-set constants and feature engineering helpers in `features.py`.
2. Update `train.py` to select named feature sets and model types, preserving current default behavior.
3. Add tests for feature-set column exclusion, lag direction, model type selection, and bundle metadata.
4. Create `scripts/benchmark_models.py` using existing time-ordered event-window logic.
5. Add benchmark-script tests with synthetic frames and skipped invalid folds.
6. Run focused tests, then full tests.
7. Run the required Phase 3 benchmark command for `CATHROCK`, 60 days, 6h horizon.
8. Inspect benchmark, best-model bundle, and feature-importance outputs.
9. Update README and write `chatref/phase3_performance_report.md`.
10. Review git status/diff and commit only Phase 3-owned changes if validation passes.

## 6. Validation Commands

```bash
python -m pytest tests/test_features.py tests/test_train.py tests/test_benchmark_models.py -q
```

```bash
python -m pytest -q
```

```bash
python scripts/benchmark_models.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --feature-sets time_only time_weather time_weather_dispatch_lag time_weather_constraint_lag \
  --model-types logistic_regression random_forest hist_gradient_boosting \
  --out artifacts/performance_benchmark.csv \
  --best-model-out artifacts/cathrock_6h_best_model.joblib \
  --report-out chatref/phase3_performance_report.md
```

```bash
python -m pytest -q
```

## 7. Expected Outputs

- `artifacts/performance_benchmark.csv` with one row per feature-set/model combination and mean fold metrics.
- `artifacts/cathrock_6h_best_model.joblib` containing `feature_set`, `model_type`, `feature_columns`, metrics, and existing planned-constraint metadata where feasible.
- `artifacts/cathrock_6h_feature_importance.csv` with linear coefficients, tree importances, or a clearly labelled fallback.
- `chatref/phase3_performance_report.md` with timestamp, commands, test result, benchmark result, best setting, comparison to Phase 2/2.5, limitations, and artifact paths.
- README Phase 3 section with benchmark command, result summary, and honest limitations.

## 8. Acceptance Criteria

- Feature-set helpers never include `target_constrained`, `target_constrained_next_*`, or non-numeric/string label columns as model features.
- Lag/rolling constraint features use shifted historical values only.
- Dispatch lag features use current-or-past dispatch fields only.
- `train.py` supports `--feature-set` and `--model-type` with backward-compatible defaults.
- Invalid event-window folds are skipped with recorded reasons, not crashed.
- `scripts/benchmark_models.py` writes the requested CSV schema even when some combinations have skipped folds.
- Best model bundle includes `feature_set` and requested `model_type`.
- `python -m pytest -q` passes.
- The required benchmark command completes and writes non-empty benchmark, best-model, feature-importance, and report artifacts.
- Git status/diff are reviewed and only Phase 3-owned files are staged/committed if a commit is made.

## 9. Final Report Format

- Summary
- Files changed
- Tests
- Benchmark result
- Best model
- Best feature set
- Whether performance improved
- Output artifacts
- Git status/commit
- Next recommended action
