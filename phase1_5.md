# Phase 1.5 - Expanded Target Discovery and Forecast-Horizon Labels

## Objective

Phase 1 found that the current 5-minute `target_constrained` label is too sparse for a convincing ML demo. The scan
found positive samples, but no DUID/window met the default recommendation thresholds.

Phase 1.5 expands the search and changes the target from "is constrained in this 5-minute interval" to "will be
constrained within the next forecast horizon".

## Why This Phase Is Needed

The internship project is about forecasting when a DUID will experience a network-related constraint.

A direct 5-minute binding label is technically valid but often too sparse:

```text
target_constrained = 1 only when a relevant GENCONID is binding in the current dispatch interval
```

For a forecasting service, better demo labels are:

```text
target_constrained_next_1h = 1 if a relevant constraint binds within the next 1 hour
target_constrained_next_3h = 1 if a relevant constraint binds within the next 3 hours
target_constrained_next_6h = 1 if a relevant constraint binds within the next 6 hours
```

The implementation uses future intervals only and excludes the current interval.

## Success Criteria

Minimum success:

```text
At least one DUID/horizon pair has positive_intervals > 0.
```

Preferred success:

```text
At least one DUID/horizon pair satisfies:
positive_intervals >= 20
0.005 <= positive_rate <= 0.25
relevant_genconid_count > 0
```

## Non-Goals

Do not:

- add a UI
- add deep learning
- build a production system
- fake scan results
- commit large raw AEMO data
- guess DUID coordinates
- optimise model performance before choosing a target

## Required Commands

Run expanded scan:

```bash
python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --horizons-hours 1 3 6 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --top-out artifacts/duid_constraint_scan_expanded_top.csv \
  --verbose
```

Run tests:

```bash
python -m pytest -q
```

Inspect top results:

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

## Deliverables

Required files:

```text
src/aemo_constraint_demo/scan.py
scripts/find_constrained_duids.py
tests/test_scan.py
README.md
phase1_5.md
chatref/phase1_5_horizon_scan_report.md
```

Required artifacts:

```text
artifacts/duid_constraint_scan_expanded.csv
artifacts/duid_constraint_scan_expanded_top.csv
```

## Label Definition

For a 5-minute dispatch interval dataset:

```text
1 hour = 12 intervals
3 hours = 36 intervals
6 hours = 72 intervals
```

For each row at time `t`:

```text
target_constrained_next_1h = 1 if any target_constrained == 1 in intervals t+1 through t+12
target_constrained_next_3h = 1 if any target_constrained == 1 in intervals t+1 through t+36
target_constrained_next_6h = 1 if any target_constrained == 1 in intervals t+1 through t+72
```

## Leakage Rule

Features must not use future data. The horizon label may look forward, but feature columns must be current-or-past
only.

## Recommendation Logic

For horizon-based labels:

```text
recommended = True only if:
status == "ok"
relevant_genconid_count > 0
positive_intervals >= 20
positive_rate >= 0.005
positive_rate <= 0.25
```

## Phase 2 Handoff

If a recommended DUID/horizon pair is found, Phase 2 should train a model against that horizon label.

Example:

```bash
python -m src.aemo_constraint_demo.train \
  --duid <SELECTED_DUID> \
  --lat <LAT> \
  --lon <LON> \
  --days <DAYS> \
  --horizon-hours <HORIZON> \
  --model-out artifacts/<selected_duid>_<horizon>h.joblib
```

If `train.py` does not yet support `--horizon-hours`, Phase 2 must add that first.
