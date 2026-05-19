# Phase 1 Target Discovery Execution Plan

Project path: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`
Source docs: `/Users/liuzhenqian/Desktop/aemo-network-constraint-demo/doc/phase1.md`
Source Codex Lead prompt: extracted

## 1. Current Repository State

- Existing package: `src/aemo_constraint_demo/`
- Existing AEMO client: `aemo_client.py` downloads MMSDM standing data and rolling Next Day Dispatch ZIPs.
- Existing feature logic: `features.py` maps `DUID -> CONNECTIONPOINTID -> GENCONID` and builds interval labels from `DISPATCHLOAD` and `DISPATCHCONSTRAINT`.
- Existing trainer: `train.py` trains one DUID model and already falls back to `ConstantProbabilityClassifier` for one-class windows.
- Existing tests are offline and use synthetic or monkeypatched data.
- Existing artifacts include `artifacts/oakey1sf.joblib`; existing cache files are under `data/cache/` and must not be committed as final raw outputs.

## 2. Scope

- Add a reusable Phase 1 scan module for DUID/window constraint-label statistics.
- Add a bounded CLI scan script.
- Add focused offline tests for recommendation logic, missing mappings, interval counting, ranking, and CLI smoke behavior.
- Add small CSV summary artifacts if the bounded scan can run.
- Add a short README section for target discovery.
- Add a root `phase1.md` summary document pointing to the executable plan.
- Add `chatref/phase1_constraint_scan_report.md` with validation and scan outcomes.

## 3. Out of Scope

- No UI, database, deep learning, power-flow modelling, large raw data outputs, or fake scan results.
- No changes to the training CLI contract except optional reuse of the new scan helpers if a minimal compatibility-safe change is useful.
- No guessed DUID coordinates or optional model training unless a recommended DUID and known coordinates are available.

## 4. Files to Create or Update

- Create `src/aemo_constraint_demo/scan.py`
- Create `scripts/find_constrained_duids.py`
- Create `tests/test_scan.py`
- Update `src/aemo_constraint_demo/__init__.py`
- Update `README.md`
- Create `phase1.md`
- Create `artifacts/duid_constraint_scan.csv` if scan completes, otherwise document failure
- Create `artifacts/duid_constraint_scan_top.csv` if scan results exist
- Create `chatref/phase1_constraint_scan_report.md`

## 5. Implementation Order

1. Implement `scan.py` using existing `aemo_client` and `features` helpers.
2. Add recommendation thresholds and ranking helpers.
3. Add the CLI wrapper with DUID list resolution, per-row error isolation, full CSV output, and top CSV output.
4. Add tests with monkeypatched/small in-memory data.
5. Update README and root phase doc.
6. Run test suite.
7. Run the bounded scan command from the source prompt.
8. Write the final report from observed results only.

## 6. Validation Commands

```bash
python -m pytest -q
```

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

If the scan is too slow or NEMWeb download fails, record the exact failure and provide the rerun command.

## 7. Expected Outputs

- `artifacts/duid_constraint_scan.csv` with one row per DUID/window.
- `artifacts/duid_constraint_scan_top.csv` sorted with recommended rows first.
- `chatref/phase1_constraint_scan_report.md` with commands, test result, scan output, recommended DUIDs, issues, and next command.

## 8. Acceptance Criteria

- `src/aemo_constraint_demo/scan.py` contains reusable scan logic.
- `scripts/find_constrained_duids.py` exists and runs.
- `tests/test_scan.py` covers the required Phase 1 behavior.
- `python -m pytest -q` passes.
- Scan artifacts are generated or scan failure is clearly reported without fabricated data.
- README includes `Phase 1: Target Discovery`.
- Final report identifies the best next DUID/window or clearly explains why no valid target was found.

## 9. Final Report Format

- Short summary
- Files changed
- Commands executed
- Test result
- Scan result and artifact paths
- Recommended DUIDs
- Data or implementation issues
- Acceptance criteria status
- Next command to run
