下面给你两部分：

1. **可直接复制给 Codex Lead 的提示词**
2. **`phase1.md` 的完整计划文件内容**

基于 phase0 当前状态：代码已经能跑，`pytest` 通过，`OAKEY1SF` 14 天训练窗口也成功生成模型；但正样本比例为 `0.0`，模型退化为 `constant_probability_baseline`，所以 Phase 1 的核心是找到真正发生过约束绑定的 DUID / 时间窗口。

---

# 1. Codex Lead 提示词

建议直接复制下面整段给 Codex Lead。

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Project context:
This is a small GitHub-ready demo for forecasting AEMO/NEM network constraints for a given DUID. The demo currently has:
- A clear notebook
- AEMO public data download/parsing logic
- DUID -> connection point -> relevant GENCONID mapping
- A baseline ML training script
- A FastAPI query endpoint
- README and tests

Phase0 status:
- Training command now runs successfully.
- pytest passes.
- OAKEY1SF with a 14-day window has zero positive target_constrained samples.
- Because there is only one class, training falls back to ConstantProbabilityClassifier.
- This is useful as a runnable baseline but not yet a convincing ML forecasting demo.

Your Phase1 mission:
Turn the project from a runnable baseline into a target-discovery demo that can identify at least one DUID/time window with real positive constraint-binding samples.

Do NOT over-engineer.
Do NOT build a large production system.
Do NOT add deep learning.
Do NOT add a UI.
Do NOT download or commit large raw data files.
Do NOT fake results.
Do NOT silently ignore failed downloads.
Do NOT make OAKEY1SF the only target.

Main deliverable:
Create a target discovery workflow that scans candidate DUIDs and historical windows, computes positive constraint-binding label statistics, ranks candidates, and writes a clear report.

Required implementation:

1. Add a reusable scan module:
   src/aemo_constraint_demo/scan.py

   It should contain pure/reusable functions for:
   - resolving DUID metadata
   - mapping DUID -> connection point
   - mapping connection point -> relevant GENCONIDs
   - loading dispatch constraint data for a historical window
   - computing whether each dispatch interval has any binding relevant constraint
   - summarising positive sample statistics
   - ranking/recommending candidate DUIDs

   If existing train.py already contains this logic, refactor carefully to avoid duplication.
   Keep backwards compatibility with the existing train command.

2. Add a CLI script:
   scripts/find_constrained_duids.py

   Required arguments:
   - --days: one or more integers, e.g. --days 30 60 90
   - --out: output CSV path, default artifacts/duid_constraint_scan.csv
   - --duids: optional comma-separated DUID list
   - --duids-file: optional file containing one DUID per line
   - --max-duids: optional cap to avoid huge scans
   - --min-positive-samples: default 20
   - --min-positive-rate: default 0.005
   - --max-positive-rate: default 0.10
   - --verbose

   Behaviour:
   - If --duids or --duids-file is provided, scan those DUIDs.
   - If neither is provided, build a candidate list from available AEMO unit detail data where possible.
   - If automatic candidate discovery is not reliable, fall back to a small editable candidate list file:
     data/candidate_duids.txt
     but validate each DUID against DUDETAIL before scanning.
   - For each DUID and each window in --days, produce one output row.

3. Output CSV fields:
   - duid
   - days
   - status
   - regionid
   - stationid or station_name if available
   - connection_point_id
   - relevant_genconid_count
   - total_intervals
   - positive_intervals
   - positive_rate
   - unique_binding_genconid_count
   - first_positive_dispatch_interval
   - last_positive_dispatch_interval
   - recommended
   - recommendation_reason

   Recommendation logic:
   recommended = True only if:
   - status == "ok"
   - positive_intervals >= min_positive_samples
   - positive_rate >= min_positive_rate
   - positive_rate <= max_positive_rate
   - relevant_genconid_count > 0

4. Add sample output artifacts:
   - artifacts/duid_constraint_scan.csv
   - artifacts/duid_constraint_scan_top.csv

   Do not commit large raw data.
   It is acceptable to commit small CSV summaries.

5. Add tests:
   - tests/test_scan.py

   Minimum tests:
   - recommendation logic works
   - zero positive sample DUID is not recommended
   - missing connection point is handled with status != "ok"
   - multiple relevant GENCONIDs binding in same interval count as one positive interval
   - CLI can run with mocked/small data if feasible

6. Update README minimally:
   Add a short section:
   "Phase 1: Target Discovery"
   Explain that before training a meaningful classifier, we scan DUIDs/windows to find cases with real positive constraint labels.

7. Add a project plan file:
   phase1.md

   Use the plan provided by the user.
   Keep it specific and actionable.

8. Create a final execution report:
   chatref/phase1_constraint_scan_report.md

   The report must include:
   - timestamp
   - commands run
   - files changed
   - test result
   - scan command used
   - scan output path
   - top recommended DUIDs
   - whether at least one DUID had positive samples
   - whether at least one DUID met recommendation criteria
   - any download/data issues
   - next recommended training command

Execution requirements:

Run:
python -m pytest -q

Then run a bounded scan, for example:
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose

If this scan is too slow or NEMWeb download fails:
- Still finish the implementation and tests.
- Record the exact failure in chatref/phase1_constraint_scan_report.md.
- Do not fake scan results.
- Provide the exact command the user should rerun locally.

Optional, only if a recommended DUID is found and coordinates are known:
Run training for that DUID:
python -m src.aemo_constraint_demo.train \
  --duid <SELECTED_DUID> \
  --lat <LAT> \
  --lon <LON> \
  --days <DAYS> \
  --model-out artifacts/<selected_duid>.joblib

If coordinates are not known, do not guess. Record that Phase2 needs coordinates or a mapping file.

Final response format:
- Short summary
- Files changed
- Test result
- Scan result
- Recommended DUIDs
- Next command to run
```

---

# 2. `phase1.md` 计划文件

下面这段可以直接保存为：

```text
phase1.md
```

---

````md
# Phase 1 — DUID Target Discovery for AEMO Constraint Forecasting Demo

## 1. Objective

Phase 1 upgrades the demo from a runnable baseline into a useful ML forecasting prototype by finding at least one DUID and historical window with real positive network-constraint binding samples.

The current demo can train and serve a model, but the initial example target `OAKEY1SF` over 14 days has no positive `target_constrained` samples. This causes the training pipeline to fall back to a constant-probability baseline. Phase 1 focuses on identifying better DUID targets before improving model complexity.

## 2. Main Question

Which DUIDs have enough observed relevant constraint-binding events in recent AEMO dispatch data to support a meaningful baseline ML classifier?

## 3. Success Criteria

Phase 1 is successful if the project can produce a CSV scan report showing, for each candidate DUID and historical window:

- Relevant connection point
- Relevant GENCONIDs
- Total dispatch intervals
- Positive constrained intervals
- Positive sample rate
- First and last positive interval
- Recommendation flag

Minimum success:

```text
At least one scanned DUID has positive_intervals > 0.
````

Preferred success:

```text
At least one scanned DUID satisfies:
positive_intervals >= 20
0.005 <= positive_rate <= 0.10
relevant_genconid_count > 0
```

If no DUID meets the preferred criteria, Phase 1 is still useful if it produces a reliable scan workflow and clearly reports the data limitations.

## 4. Non-Goals

Phase 1 should not attempt to build a production-grade forecasting system.

Do not do the following in Phase 1:

* Do not build a UI.
* Do not add deep learning.
* Do not model full constraint equations.
* Do not model power flow or network topology.
* Do not forecast specific transmission-line flows.
* Do not add a database.
* Do not commit large raw AEMO datasets.
* Do not fake scan or model results.
* Do not optimise model accuracy before finding a target with positive labels.

## 5. Deliverables

### Required files

```text
src/aemo_constraint_demo/scan.py
scripts/find_constrained_duids.py
tests/test_scan.py
phase1.md
chatref/phase1_constraint_scan_report.md
```

### Required output artifacts

```text
artifacts/duid_constraint_scan.csv
artifacts/duid_constraint_scan_top.csv
```

Optional, only if a good DUID is found and training coordinates are known:

```text
artifacts/<selected_duid>.joblib
artifacts/<selected_duid>_metrics.json
artifacts/example_response.json
```

## 6. Data Definition

For each DUID:

1. Resolve the DUID from AEMO unit detail data.
2. Map the DUID to its connection point.
3. Map the connection point to relevant generic constraints.
4. Load historical dispatch constraint data.
5. For each dispatch interval, mark:

```text
target_constrained = 1
```

if at least one relevant GENCONID is binding in that interval.

Otherwise:

```text
target_constrained = 0
```

The output is a DUID-level binary label:

```text
Will this DUID be affected by at least one relevant network constraint in this dispatch interval?
```

## 7. Recommendation Logic

A DUID/window pair should be recommended only if all of the following are true:

```text
status == "ok"
relevant_genconid_count > 0
positive_intervals >= min_positive_samples
positive_rate >= min_positive_rate
positive_rate <= max_positive_rate
```

Default thresholds:

```text
min_positive_samples = 20
min_positive_rate = 0.005
max_positive_rate = 0.10
```

Reasoning:

* If positive samples are zero, there is no classification problem.
* If positive samples are too few, validation metrics are unstable.
* If positive rate is too high, the event may not represent a sparse network-constraint risk case.
* A moderate sparse positive rate is better for demonstrating imbalanced classification.

## 8. Output CSV Schema

The scan script must write:

```text
duid
days
status
regionid
stationid
station_name
connection_point_id
relevant_genconid_count
total_intervals
positive_intervals
positive_rate
unique_binding_genconid_count
first_positive_dispatch_interval
last_positive_dispatch_interval
recommended
recommendation_reason
```

### Status values

Suggested status values:

```text
ok
missing_duid
missing_connection_point
no_relevant_genconids
no_dispatch_data
download_failed
parse_failed
error
```

Do not crash the full scan because one DUID fails. Record the failure in the row and continue.

## 9. CLI Specification

Create:

```text
scripts/find_constrained_duids.py
```

### Basic command

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

### Explicit DUID list

```bash
python scripts/find_constrained_duids.py \
  --duids DUID1,DUID2,DUID3 \
  --days 30 60 90 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

### DUID file

```bash
python scripts/find_constrained_duids.py \
  --duids-file data/candidate_duids.txt \
  --days 30 60 90 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

### Required arguments

```text
--days
```

### Optional arguments

```text
--out
--duids
--duids-file
--max-duids
--min-positive-samples
--min-positive-rate
--max-positive-rate
--verbose
```

## 10. Candidate DUID Strategy

Preferred approach:

1. Build candidate DUIDs from AEMO unit detail data.
2. Prioritise dispatchable and semi-scheduled units.
3. Prefer renewable and remote generators if metadata allows.
4. Validate all candidates before scanning.

Fallback approach:

Use an editable file:

```text
data/candidate_duids.txt
```

Rules:

* The file should be small.
* Do not assume every listed DUID is valid.
* Validate each candidate against current AEMO metadata.
* Invalid DUIDs should produce a scan row with `status != "ok"`.

## 11. Implementation Plan

### Step 1 — Refactor reusable logic

Create:

```text
src/aemo_constraint_demo/scan.py
```

Move or reuse logic from training code where appropriate.

Recommended functions:

```python
def resolve_duid_metadata(duid: str) -> dict:
    ...

def get_connection_point_for_duid(duid: str) -> str | None:
    ...

def get_relevant_genconids(connection_point_id: str) -> set[str]:
    ...

def load_dispatch_constraints_for_window(days: int) -> pandas.DataFrame:
    ...

def build_constraint_labels_for_duid(duid: str, days: int) -> pandas.DataFrame:
    ...

def summarise_duid_window(duid: str, days: int, thresholds: ScanThresholds) -> dict:
    ...

def rank_scan_results(results: pandas.DataFrame) -> pandas.DataFrame:
    ...
```

Use dataclasses if helpful, but keep the code simple.

### Step 2 — Add CLI wrapper

Create:

```text
scripts/find_constrained_duids.py
```

Responsibilities:

* Parse CLI arguments.
* Resolve candidate DUIDs.
* Run scan for every DUID/window pair.
* Write full CSV.
* Write top recommended CSV.
* Print concise progress logs.
* Continue on per-DUID failures.
* Return non-zero only for severe global failures.

### Step 3 — Add tests

Create:

```text
tests/test_scan.py
```

Minimum tests:

1. Zero positive samples are not recommended.
2. DUID with enough positive samples and valid positive rate is recommended.
3. DUID with too few positives is not recommended.
4. DUID with positive rate above max threshold is not recommended.
5. Multiple binding GENCONIDs in the same interval count as one positive interval.
6. Missing connection point returns status `missing_connection_point`.
7. Ranking puts recommended candidates first.

### Step 4 — Generate output artifacts

Run:

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

Also create:

```text
artifacts/duid_constraint_scan_top.csv
```

The top CSV should contain:

* Recommended rows first
* Highest positive interval count
* Reasonable positive rate
* Most recent positive interval

### Step 5 — Update README

Add a section:

```md
## Phase 1: Target Discovery
```

Include:

* Why target discovery is necessary
* How to run the scan
* How to interpret `positive_rate`
* Why zero-positive DUIDs fall back to a constant baseline
* How to choose the next training target

### Step 6 — Write execution report

Create:

```text
chatref/phase1_constraint_scan_report.md
```

The report must include:

```text
timestamp
project directory
phase objective
files changed
commands run
pytest result
scan command
scan output path
top DUIDs by positive_intervals
recommended DUIDs
whether a valid target was found
issues encountered
next recommended training command
```

## 12. Validation Commands

Run tests:

```bash
python -m pytest -q
```

Run scan:

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

Inspect results:

```bash
python - <<'PY'
import pandas as pd

df = pd.read_csv("artifacts/duid_constraint_scan.csv")
print(df.sort_values(
    ["recommended", "positive_intervals", "positive_rate"],
    ascending=[False, False, False]
).head(20))
PY
```

## 13. Expected Phase 1 Final Output

The final report should clearly answer:

```text
Did we find DUIDs with positive constraint samples?
Which DUID/window is best for Phase 2 training?
Is there enough data to train a real classifier?
What command should be run next?
```

Example final recommendation format:

```text
Recommended DUID for Phase 2:
DUID: <DUID>
Days: 60
Connection point: <CONNECTION_POINT>
Relevant GENCONIDs: <N>
Positive intervals: <N>
Positive rate: <RATE>
Last positive interval: <TIMESTAMP>
Reason: enough sparse positive samples for baseline ML classification
```

## 14. Phase 2 Handoff

If a recommended DUID is found, Phase 2 should train a real classifier:

```bash
python -m src.aemo_constraint_demo.train \
  --duid <SELECTED_DUID> \
  --lat <LAT> \
  --lon <LON> \
  --days <DAYS> \
  --model-out artifacts/<selected_duid>.joblib
```

Phase 2 should then evaluate:

```text
average_precision
roc_auc
positive_rate
calibration
top-risk intervals
example API response
```

If coordinates are not available, Phase 2 must first add a small coordinate mapping file:

```text
data/duid_coordinates.csv
```

Suggested schema:

```text
duid
lat
lon
source
notes
```

Do not guess coordinates silently.

## 15. Risk Register

### Risk 1 — NEMWeb download is slow or unstable

Mitigation:

* Use small bounded scans first.
* Add `--max-duids`.
* Cache downloaded files.
* Log failed downloads.
* Do not fake results.

### Risk 2 — No DUID has enough positive samples in short windows

Mitigation:

* Try 90 days.
* Try more DUIDs.
* Prioritise generators known to be affected by constraints.
* Record that Phase 2 needs a larger historical window.

### Risk 3 — Mapping DUID to GENCONID is incomplete

Mitigation:

* Record `relevant_genconid_count`.
* Mark rows with no mapping as `no_relevant_genconids`.
* Do not recommend unmapped DUIDs.

### Risk 4 — Positive rate is extremely low

Mitigation:

* Use Average Precision rather than accuracy in Phase 2.
* Consider threshold tuning.
* Use longer windows.
* Keep the demo honest about class imbalance.

## 16. Definition of Done

Phase 1 is done when:

* `scripts/find_constrained_duids.py` exists and runs.
* `src/aemo_constraint_demo/scan.py` contains reusable scan logic.
* `tests/test_scan.py` passes.
* `python -m pytest -q` passes.
* `artifacts/duid_constraint_scan.csv` is generated or a failure is clearly reported.
* `artifacts/duid_constraint_scan_top.csv` is generated if scan results exist.
* `chatref/phase1_constraint_scan_report.md` exists.
* README has a short Phase 1 section.
* The final report identifies the best next DUID/window or clearly explains why none was found.

````

---

# 你现在应该怎么用

在项目根目录执行：

```bash
cd /Users/liuzhenqian/Desktop/aemo-network-constraint-demo
touch phase1.md
````

把上面的 `phase1.md` 内容放进去。

然后把 **Codex Lead 提示词** 发给 Codex，让它按计划执行。执行完之后，重点看这三个文件：

```text
artifacts/duid_constraint_scan.csv
artifacts/duid_constraint_scan_top.csv
chatref/phase1_constraint_scan_report.md
```

Phase 1 完成后，下一步就是从 `duid_constraint_scan_top.csv` 里选一个推荐 DUID，进入真正的 ML 训练与 API demo polish。
