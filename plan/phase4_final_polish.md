# Phase 4 Final GitHub Polish Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase4.md`
Source Codex Lead prompt: extracted from the fenced `给 Codex Lead 的 Phase 4 提示词` block in `doc/phase4.md`

## 1. Current Repository State

- The repository is a Git worktree.
- Pre-existing uncommitted state before Phase 4 edits:
  - deleted: `chatref/phase0/codex_execute_report.md`, `phase1.md`, `phase1_5.md`
  - untracked: `chatref/codex_execute_report.md`, `doc/phase3.md`, `doc/phase4.md`
- `app.py` already declares `horizon_hours: int | None = Query(default=None, ge=1, le=72)`.
- `src/aemo_constraint_demo/forecast.py` already infers `horizon_hours` from the model bundle when omitted and raises a `ValueError` when an explicit horizon mismatches the bundle.
- `tests/test_api.py` and `tests/test_forecast.py` already cover inferred horizon and mismatched-horizon behavior.
- `README.md` includes Phase 3 metrics and API commands, but the top can be tightened with a 60-second demo result summary.
- `artifacts/example_cathrock_6h_response.json` currently reports `balanced_logistic_regression`, so it appears to have been generated from the older `artifacts/cathrock_6h.joblib` bundle instead of `artifacts/cathrock_6h_best_model.joblib`.
- `notebooks/aemo_constraint_forecast_demo.ipynb` is cleared, but still tells a Phase 2.5 story and uses old model/artifact paths.
- `application/` does not exist yet.
- `data/cache/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, and `artifacts/*.joblib` are ignored. Tracked small CSV/JSON artifacts under `artifacts/` are allowed.

## 2. Scope

- Verify API consistency for best-model horizon inference and mismatch handling.
- Regenerate `artifacts/example_cathrock_6h_response.json` from `artifacts/cathrock_6h_best_model.joblib`.
- Polish the README top section with the selected target, horizon, model, benchmark metrics, internship mapping, and limitations.
- Update the notebook to tell the final Phase 4 story and reference the best model and Phase 3 benchmark artifacts.
- Create application support materials:
  - `application/project_summary.md`
  - `application/resume_bullets.md`
  - `application/interview_talking_points.md`
- Run the required tests and API smoke checks.
- Write `chatref/phase4_final_polish_report.md`.
- Review git hygiene with `git status` and `git diff --stat`.

## 3. Out of Scope

- No new modelling complexity.
- No deep learning.
- No UI.
- No database.
- No fabricated metrics.
- No committing large raw AEMO data, local caches, virtualenvs, or Python caches.
- No cleanup of pre-existing unrelated deletions unless explicitly requested.

## 4. Files to Create or Update

- Create `plan/phase4_final_polish.md`
- Update `README.md`
- Update `notebooks/aemo_constraint_forecast_demo.ipynb`
- Refresh `artifacts/example_cathrock_6h_response.json`
- Create `application/project_summary.md`
- Create `application/resume_bullets.md`
- Create `application/interview_talking_points.md`
- Create `chatref/phase4_final_polish_report.md`

## 5. Implementation Order

1. Complete the repository consistency audit and record findings in this plan.
2. Refresh the best-model API example using `forecast_from_bundle` with a deterministic start timestamp.
3. Update README with a concise demo result summary and ensure API commands reference the best model.
4. Update the notebook markdown and constants to reference Phase 3 best-model artifacts, the benchmark result, and final limitations; keep outputs cleared.
5. Create compact application materials for project summary, resume bullets, and interview talking points.
6. Run `python -m pytest -q`.
7. Run the required inferred-horizon and mismatched-horizon API smoke snippets.
8. Write the final Phase 4 report.
9. Review `git status` and `git diff --stat` for hygiene.

## 6. Validation Commands

```bash
python -m pytest -q
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
    },
)
print(response.status_code)
payload = response.json()
print(payload["duid"], payload["horizon_hours"], payload["model"]["model_type"], len(payload["forecast"]))
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
        "horizon_hours": 24,
        "model_path": "artifacts/cathrock_6h_best_model.joblib",
    },
)
print(response.status_code)
print(response.text[:300])
PY
```

```bash
git status
git diff --stat
```

## 7. Expected Outputs

- `artifacts/example_cathrock_6h_response.json` reports `model.model_type == "logistic_regression"` and `horizon_hours == 6`.
- README first screen explains the demo, internship mapping, CATHROCK 6h target, best model, metrics, and limitations.
- Notebook references `artifacts/cathrock_6h_best_model.joblib`, `artifacts/cathrock_6h_best_model_metrics.json`, `artifacts/performance_benchmark.csv`, and the best-model API example.
- Application material files exist and are concise.
- Final report exists at `chatref/phase4_final_polish_report.md`.

## 8. Acceptance Criteria

- API supports `GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib`.
- API supports `GET /forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h_best_model.joblib`.
- API returns status `400` for explicit `horizon_hours=24` against the 6h best-model bundle.
- `python -m pytest -q` passes.
- No large raw AEMO files, cache directories, virtualenvs, or Python caches are introduced into tracked changes.
- README and notebook do not overstate production accuracy.
- Final report includes timestamp, changed files, test results, smoke results, final artifacts, remaining limitations, GitHub readiness, and recommended git commands.

## 9. Final Report Format

- Summary
- Files changed
- Tests
- API smoke result
- README/notebook status
- Application materials
- Git status summary
- Ready for GitHub: yes/no
- Next exact commands
