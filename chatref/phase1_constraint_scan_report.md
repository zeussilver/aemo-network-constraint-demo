# Phase 1 Constraint Scan Report

Timestamp: `2026-05-19 21:25:35 AEST`
Project directory: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`

## Objective

Create and validate a DUID target-discovery workflow that scans candidate DUIDs and recent dispatch windows for real positive constraint-binding labels.

## Files Changed

- `plan/phase1_execution_plan.md`
- `src/aemo_constraint_demo/scan.py`
- `scripts/find_constrained_duids.py`
- `tests/test_scan.py`
- `src/aemo_constraint_demo/__init__.py`
- `data/candidate_duids.txt`
- `README.md`
- `phase1.md`
- `artifacts/duid_constraint_scan.csv`
- `artifacts/duid_constraint_scan_top.csv`
- `chatref/phase1_constraint_scan_report.md`

## Commands Run

```bash
python -m pytest tests/test_scan.py -q
python -m py_compile src/aemo_constraint_demo/scan.py scripts/find_constrained_duids.py
python -m pytest -q
python scripts/find_constrained_duids.py --days 30 60 --max-duids 50 --out artifacts/duid_constraint_scan.csv --verbose
python -m pytest -q
```

## Test Result

```text
34 passed in 1.31s
```

## Scan Result

Scan command:

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

Output paths:

- `artifacts/duid_constraint_scan.csv`
- `artifacts/duid_constraint_scan_top.csv`

Observed summary:

- Scan rows: `100`
- Status rows: `ok = 100`
- Rows with positive samples: `9`
- Recommended rows: `0`
- At least one DUID had positive samples: `yes`
- At least one DUID met recommendation criteria: `no`

Top positive rows:

| duid | days | connection_point_id | relevant_genconid_count | total_intervals | positive_intervals | positive_rate | unique_binding_genconid_count | last_positive_dispatch_interval |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| BULGANA1 | 60 | VBGT1B | 221 | 17280 | 9 | 0.000521 | 2 | 2026-04-17 06:50:00 |
| GSWF1A | 30 | SROB1G | 87 | 8640 | 5 | 0.000579 | 1 | 2026-04-23 08:05:00 |
| GSWF1A | 60 | SROB1G | 87 | 17280 | 5 | 0.000289 | 1 | 2026-04-23 08:05:00 |
| WOOLGSF1 | 60 | QWLS1W | 127 | 17280 | 5 | 0.000289 | 2 | 2026-04-14 11:30:00 |
| WAMBOWF2 | 30 | QDMR1W | 14 | 6096 | 4 | 0.000656 | 1 | 2026-04-29 09:40:00 |

## Recommended DUIDs

No DUID/window met the default Phase 1 recommendation thresholds:

```text
positive_intervals >= 20
0.005 <= positive_rate <= 0.10
relevant_genconid_count > 0
```

The best observed row was `BULGANA1` over `60` days with `9` positive intervals and `positive_rate = 0.000521`, which is below both the minimum positive-sample and minimum positive-rate thresholds.

## Data Issues

- No NEMWeb download failure occurred during the bounded scan.
- The current `DUDETAIL` table used by this repo does not expose populated `regionid`, `stationid`, or `station_name` columns, so those CSV fields are blank.
- Several scanned DUIDs have positive labels, but all positive rates are much lower than the default preferred lower bound.
- `WAMBOWF2` had only `6096` dispatch intervals in both 30-day and 60-day rows, meaning it did not appear in all loaded dispatch intervals.

## Next Step

Do not proceed to Phase 2 model training from this scan yet. First expand target discovery:

```bash
python scripts/find_constrained_duids.py \
  --days 60 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --verbose
```

If a later scan finds a recommended DUID and coordinates are available, then run:

```bash
python -m src.aemo_constraint_demo.train \
  --duid <SELECTED_DUID> \
  --lat <LAT> \
  --lon <LON> \
  --days <DAYS> \
  --model-out artifacts/<selected_duid>.joblib
```

Coordinates should not be guessed; add `data/duid_coordinates.csv` first if needed.

## Acceptance Criteria Status

- `scripts/find_constrained_duids.py` exists and runs: `pass`
- `src/aemo_constraint_demo/scan.py` contains reusable scan logic: `pass`
- `tests/test_scan.py` exists and passes: `pass`
- `python -m pytest -q` passes: `pass`
- `artifacts/duid_constraint_scan.csv` generated: `pass`
- `artifacts/duid_constraint_scan_top.csv` generated: `pass`
- README has a Phase 1 section: `pass`
- Final report identifies best next DUID/window or explains why none was found: `pass`
