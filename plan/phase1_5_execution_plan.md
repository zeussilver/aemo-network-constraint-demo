# Phase 1.5 Expanded Target Discovery Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase1.5.md`
Source Codex Lead prompt: extracted

## 1. Current Repository State

- Phase 1 scan logic lives in `src/aemo_constraint_demo/scan.py`.
- The scan CLI lives in `scripts/find_constrained_duids.py` and currently supports current-interval labels only.
- Current scan output columns do not include `horizon_hours`.
- `src/aemo_constraint_demo/features.py` builds `target_constrained` from current dispatch intervals.
- `tests/test_scan.py` already covers current recommendation logic, zero-positive rows, duplicate constraints in one interval, ranking, and a CLI smoke test.
- `README.md` has a Phase 1 target discovery section but no Phase 1.5 horizon-label section.
- `src/aemo_constraint_demo/train.py` does not currently expose `--horizon-hours`, so model training against horizon labels is out of scope for this phase unless that changes in a later phase.

## 2. Scope

- Add horizon-based binary label construction for next-1h, next-3h, and next-6h targets.
- Extend scan summaries to emit one row per DUID/window/horizon.
- Add CLI options for `--horizons-hours`, `--top-out`, and `--max-positive-rate-horizon`.
- Generate expanded scan artifacts:
  - `artifacts/duid_constraint_scan_expanded.csv`
  - `artifacts/duid_constraint_scan_expanded_top.csv`
- Add focused offline tests for horizon-label behavior, horizon recommendation logic, scan output columns, and zero-positive handling.
- Add a short Phase 1.5 README section.
- Create root `phase1_5.md` from the phase specification.
- Create `chatref/phase1_5_horizon_scan_report.md` with commands, results, candidate decision, and next command.

## 3. Out of Scope

- No UI.
- No deep learning.
- No production hardening.
- No guessed DUID coordinates.
- No large raw AEMO data committed as final deliverables.
- No training CLI changes unless the existing trainer already supports horizon labels.
- No fabricated scan results if NEMWeb or local cache data is incomplete.

## 4. Files to Create or Update

- Update `src/aemo_constraint_demo/scan.py`
- Update `scripts/find_constrained_duids.py`
- Update `tests/test_scan.py`
- Update `README.md`
- Create `phase1_5.md`
- Create `artifacts/duid_constraint_scan_expanded.csv`
- Create `artifacts/duid_constraint_scan_expanded_top.csv`
- Create `chatref/phase1_5_horizon_scan_report.md`

## 5. Implementation Order

1. Add reusable horizon-label helper logic in `scan.py` while preserving current-label behavior for compatibility.
2. Extend scan output columns and summary/ranking helpers to include `horizon_hours`.
3. Update failure rows so each requested horizon gets a standards-compliant row.
4. Add CLI arguments and write top output to the user-specified `--top-out` path.
5. Add and adjust tests in `tests/test_scan.py`.
6. Update README and add `phase1_5.md`.
7. Run focused tests, then the full test suite.
8. Run the required expanded scan command.
9. Inspect top results and write the Phase 1.5 report from observed outputs.

## 6. Validation Commands

```bash
python -m pytest tests/test_scan.py -q
```

```bash
python -m pytest -q
```

```bash
python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --horizons-hours 1 3 6 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --top-out artifacts/duid_constraint_scan_expanded_top.csv \
  --verbose
```

```bash
python - <<'PY'
import pandas as pd

df = pd.read_csv("artifacts/duid_constraint_scan_expanded.csv")
cols = [
    "duid",
    "days",
    "horizon_hours",
    "status",
    "connection_point_id",
    "relevant_genconid_count",
    "positive_intervals",
    "positive_rate",
    "unique_binding_genconid_count",
    "last_positive_dispatch_interval",
    "recommended",
    "recommendation_reason",
]
print(
    df.sort_values(
        ["recommended", "positive_intervals", "positive_rate"],
        ascending=[False, False, False],
    )[cols].head(30).to_string(index=False)
)
PY
```

## 7. Expected Outputs

- Expanded scan CSV with `horizon_hours` and one row per DUID/window/horizon.
- Ranked top CSV with recommended rows first.
- Passing tests.
- Report identifying whether any recommended DUID/horizon pair was found.
- If a candidate is found, a Phase 2 training command template using the selected DUID, days, and horizon; if not, a rerun or next engineering step.

## 8. Acceptance Criteria

- `--horizons-hours 1 3 6` runs without breaking current scan behavior.
- Horizon labels use future intervals only and exclude the current interval.
- Duplicate binding constraints in the same interval count as one positive interval.
- Horizon recommendation thresholds use `max_positive_rate_horizon`.
- Scan output includes `horizon_hours`.
- Zero-positive DUID/horizon rows are not recommended.
- `python -m pytest -q` passes.
- Required expanded scan artifacts and report are created, or any scan failure is reported without fake data.

## 9. Final Report Format

- Summary
- Files changed
- Tests
- Expanded scan result
- Best DUID/horizon candidate
- Next exact command
