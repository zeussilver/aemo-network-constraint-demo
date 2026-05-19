# Phase 1.5 Horizon Scan Report

Timestamp: `2026-05-19 22:11:40 AEST`
Project directory: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`

## Objective

Implement expanded DUID target discovery with forecast-horizon labels and run the required 60/90/120 day scan across
1h, 3h, and 6h horizons.

## Files Changed

- `plan/phase1_5_execution_plan.md`
- `src/aemo_constraint_demo/scan.py`
- `scripts/find_constrained_duids.py`
- `tests/test_scan.py`
- `README.md`
- `phase1_5.md`
- `artifacts/duid_constraint_scan_expanded.csv`
- `artifacts/duid_constraint_scan_expanded_top.csv`
- `chatref/phase1_5_horizon_scan_report.md`

## Commands Run

```bash
python -m pytest tests/test_scan.py -q
python -m py_compile src/aemo_constraint_demo/scan.py scripts/find_constrained_duids.py
python -m pytest -q
python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --horizons-hours 1 3 6 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --top-out artifacts/duid_constraint_scan_expanded_top.csv \
  --verbose
```

Inspection command:

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

## Test Result

```text
38 passed in 1.32s
```

Focused scan tests:

```text
10 passed in 0.34s
```

## Expanded Scan Result

Scan output paths:

- `artifacts/duid_constraint_scan_expanded.csv`
- `artifacts/duid_constraint_scan_expanded_top.csv`

Observed summary:

- Scan rows: `1800`
- Status rows: `ok = 1791`, `no_relevant_genconids = 9`
- Rows with positive samples: `105`
- Recommended rows: `16`
- Recommended rows by horizon: `3h = 4`, `6h = 12`
- Recommended rows by window: `60 days = 7`, `90 days = 6`, `120 days = 3`

Top recommended rows:

| duid | days | horizon_hours | connection_point_id | relevant_genconid_count | positive_intervals | positive_rate | unique_binding_genconid_count | last_positive_dispatch_interval |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| CATHROCK | 60 | 6 | SCRK | 103 | 228 | 0.013194 | 3 | 2026-04-23 22:15:00 |
| CATHROCK | 90 | 6 | SCRK | 103 | 228 | 0.008796 | 3 | 2026-04-23 22:15:00 |
| CATHROCK | 120 | 6 | SCRK | 103 | 228 | 0.006597 | 3 | 2026-04-23 22:15:00 |
| BULGANA1 | 60 | 6 | VBGT1B | 221 | 151 | 0.008738 | 2 | 2026-04-17 06:45:00 |
| BULGANA1 | 90 | 6 | VBGT1B | 221 | 151 | 0.005826 | 2 | 2026-04-17 06:45:00 |
| WOOLGSF1 | 60 | 6 | QWLS1W | 127 | 147 | 0.008507 | 2 | 2026-04-14 11:25:00 |
| WOOLGSF1 | 90 | 6 | QWLS1W | 127 | 147 | 0.005671 | 2 | 2026-04-14 11:25:00 |
| MUCRKSF1 | 60 | 6 | QWLM1M | 149 | 144 | 0.008333 | 2 | 2026-04-14 11:25:00 |
| MUCRKSF1 | 90 | 6 | QWLM1M | 149 | 144 | 0.005556 | 2 | 2026-04-14 11:25:00 |
| CATHROCK | 60 | 3 | SCRK | 103 | 120 | 0.006944 | 3 | 2026-04-23 22:15:00 |

## Best Candidate

Recommended DUID/horizon found: `yes`.

Best ranked candidate:

```text
duid = CATHROCK
days = 60
horizon_hours = 6
connection_point_id = SCRK
positive_intervals = 228
positive_rate = 0.013194
unique_binding_genconid_count = 3
last_positive_dispatch_interval = 2026-04-23 22:15:00
```

This satisfies the Phase 1.5 recommendation criteria:

```text
status == "ok"
relevant_genconid_count > 0
positive_intervals >= 20
positive_rate >= 0.005
positive_rate <= 0.25
```

## Next Training Command

`src/aemo_constraint_demo/train.py` does not yet support `--horizon-hours`, so training was not run in this phase.
Phase 2 should first update the trainer to use the selected horizon label, then run a command shaped like:

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --lat <LAT> \
  --lon <LON> \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib
```

Coordinates should not be guessed. Add a vetted `data/duid_coordinates.csv` or another explicit coordinate source
before running weather-backed training for `CATHROCK`.

## Data and Implementation Issues

- No NEMWeb download or parsing failure occurred during the final expanded scan.
- `CTHLWF1` produced `no_relevant_genconids` rows for all three windows and horizons.
- The initial direct implementation reprocessed full dispatch tables per DUID/window and was too slow/high-memory for
  the required 200-DUID scan. The final implementation prefilters binding constraint rows, prefilters candidate DUID
  dispatch rows, and loads the largest requested dispatch window once before deriving smaller windows.
- The project directory is not a Git repository, so no `git status` or `git diff` output is available.

## Acceptance Criteria Status

- Expanded scan CLI supports `--horizons-hours 1 3 6`: `pass`
- CLI supports `--top-out`: `pass`
- CLI supports `--max-positive-rate-horizon`: `pass`
- Horizon labels are future-only and exclude the current interval: `pass`
- Scan output includes `horizon_hours`: `pass`
- Duplicate constraints in one interval count as one positive interval: `pass`
- Zero-positive DUID/horizon rows are not recommended: `pass`
- `python -m pytest -q` passes: `pass`
- Expanded scan artifacts generated: `pass`
- Recommended DUID/horizon found: `pass`
