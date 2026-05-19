# Phase 1 — DUID Target Discovery

Source plan: `doc/phase1.md`
Execution plan: `plan/phase1_execution_plan.md`

## Objective

Find at least one DUID and recent historical window with real positive network-constraint binding samples, so the demo can move beyond a runnable zero-positive baseline.

## Scope

- Scan candidate DUIDs across one or more rolling Next Day Dispatch windows.
- Map each DUID to its current connection point and relevant GENCONIDs.
- Count dispatch intervals where at least one relevant constraint binds.
- Recommend DUID/window rows that satisfy the Phase 1 thresholds.
- Write small CSV summaries and a final execution report.

## Non-Goals

- No UI, deep learning, database, full power-flow model, or large raw AEMO data artifacts.
- No fake scan results.
- No guessed weather coordinates for optional training.

## Recommendation Criteria

A row is recommended only when all are true:

```text
status == "ok"
relevant_genconid_count > 0
positive_intervals >= 20
positive_rate >= 0.005
positive_rate <= 0.10
```

## Main Command

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

## Expected Outputs

- `src/aemo_constraint_demo/scan.py`
- `scripts/find_constrained_duids.py`
- `tests/test_scan.py`
- `artifacts/duid_constraint_scan.csv`
- `artifacts/duid_constraint_scan_top.csv`
- `chatref/phase1_constraint_scan_report.md`

## Validation

```bash
python -m pytest -q
```
