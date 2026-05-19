下一步应该做 **Phase 1.5：扩大目标发现 + 改造标签粒度**。
现在还不应该直接进入 Phase 2 训练，因为 Phase 1 扫描虽然跑通了，但 `100` 行扫描里只有 `9` 行有正样本，`recommended rows = 0`；最好的 `BULGANA1` 也只有 `9` 个正样本，低于推荐阈值。

## 结论

当前状态是：

| 项目           | 状态           |
| ------------ | ------------ |
| 代码能跑         | 已完成          |
| 测试           | `34 passed`  |
| AEMO 下载      | 成功           |
| DUID 扫描      | 成功           |
| 找到正样本        | 找到了          |
| 找到适合训练的 DUID | 还没有          |
| 下一步          | 扩大扫描 + 改标签窗口 |

Phase 1 报告也明确建议：**不要马上进入 Phase 2 模型训练，先扩大 target discovery**。推荐命令是扫描 `60` 天、最多 `200` 个 DUID。

---

# 下一步执行路线

## Step 1：先跑扩大扫描

在项目根目录执行：

```bash
cd /Users/liuzhenqian/Desktop/aemo-network-constraint-demo

python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --verbose
```

然后看结果：

```bash
python - <<'PY'
import pandas as pd

df = pd.read_csv("artifacts/duid_constraint_scan_expanded.csv")

cols = [
    "duid",
    "days",
    "status",
    "connection_point_id",
    "relevant_genconid_count",
    "total_intervals",
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
        ascending=[False, False, False]
    )[cols].head(30).to_string(index=False)
)
PY
```

目标是找到至少一个：

```text
positive_intervals >= 20
```

如果仍然没有，进入 Step 2。

---

## Step 2：改造标签粒度，不再只预测“当前 5 分钟是否约束”

现在的问题是正样本太稀疏。对于 demo，更合理的预测目标应该是：

```text
未来 1 小时内，该 DUID 是否会遇到至少一次相关网络约束？
```

而不是：

```text
当前 5 分钟 dispatch interval 是否有 binding constraint？
```

这会把标签从极稀疏的 5 分钟事件，变成更适合业务 demo 的 forecast horizon 标签。

建议新增三个 horizon：

```text
target_constrained_next_1h
target_constrained_next_3h
target_constrained_next_6h
```

这样更贴合实习项目描述里的 “forecast when a generator or line will experience a network related constraint”。

---

# 给 Codex Lead 的下一阶段提示词

直接复制下面这段给 Codex。

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Current project status:
Phase 0 made the training and API baseline runnable.
Phase 1 added DUID target discovery.
Tests pass.
The Phase 1 bounded scan found positive constraint samples, but no DUID/window met the default recommendation criteria.

Important Phase 1 result:
- 100 scan rows
- 100 rows status ok
- 9 rows with positive samples
- 0 recommended rows
- Best observed row: BULGANA1 over 60 days with 9 positive intervals and positive_rate around 0.000521
- Therefore, do NOT proceed directly to normal Phase 2 model training yet.

Mission:
Implement Phase 1.5: expanded target discovery and forecast-horizon label engineering.

Goal:
Find a usable demo target by either:
1. scanning more DUIDs and longer windows, or
2. creating less sparse forecast-horizon labels such as “constraint occurs in the next 1h / 3h / 6h”.

Do NOT fake results.
Do NOT commit large raw AEMO data.
Do NOT add a UI.
Do NOT add deep learning.
Do NOT make the project production-grade.
Keep this a clean internship demo.

Required tasks:

1. Expand scan capability

Update `scripts/find_constrained_duids.py` and/or `src/aemo_constraint_demo/scan.py` so the scan can run:

python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --verbose

The script should continue to:
- fail gracefully per DUID
- write one row per DUID/window
- produce a top-ranked CSV
- not crash on partial data issues

Output files:
- artifacts/duid_constraint_scan_expanded.csv
- artifacts/duid_constraint_scan_expanded_top.csv

2. Add forecast-horizon labels

Add support for horizon-based binary labels.

Current label:
target_constrained = 1 if a relevant GENCONID is binding in the current dispatch interval.

New labels:
- target_constrained_next_1h
- target_constrained_next_3h
- target_constrained_next_6h

Definition:
For each dispatch interval, target_constrained_next_Xh = 1 if at least one relevant constraint binds at any point in the next X hours.

For 5-minute dispatch intervals:
- 1h = next 12 intervals
- 3h = next 36 intervals
- 6h = next 72 intervals

Important:
Avoid label leakage in features.
Features for a row must only use current or past information.
The horizon label may look forward, but features must not.

3. Update scan summaries for horizon labels

The scan output should include, for each DUID/window/horizon:

- duid
- days
- horizon_hours
- status
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

Recommendation criteria for horizon labels:
- status == "ok"
- relevant_genconid_count > 0
- positive_intervals >= 20
- positive_rate >= 0.005
- positive_rate <= 0.25

Use max_positive_rate = 0.25 for horizon labels because aggregation naturally increases positive rate.

4. Add CLI options

Add these arguments:

--horizons-hours 1 3 6
--expanded-top-out artifacts/duid_constraint_scan_expanded_top.csv
--max-positive-rate-horizon 0.25

Example command:

python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --horizons-hours 1 3 6 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --top-out artifacts/duid_constraint_scan_expanded_top.csv \
  --verbose

5. Add tests

Update or create tests in:

tests/test_scan.py

Minimum new tests:
- horizon label for 1h correctly marks earlier intervals before an event
- horizon label does not mark intervals after an event unless another future event exists
- multiple constraints in the same interval still count as one positive interval
- recommendation logic uses horizon_hours and max_positive_rate_horizon
- scan output contains horizon_hours
- zero-positive DUID remains not recommended

Run:

python -m pytest -q

6. Update README

Add a short section:

"Phase 1.5: Forecast-Horizon Target Discovery"

Explain:
- why 5-minute binding labels are too sparse
- why next-1h / next-3h / next-6h labels are better for a forecasting demo
- how to run the expanded scan
- how to pick the best DUID/horizon pair for model training

7. Create execution report

Create:

chatref/phase1_5_horizon_scan_report.md

Report must include:
- timestamp
- commands run
- files changed
- test result
- expanded scan command
- expanded scan output paths
- top DUID/horizon rows
- whether a recommended DUID/horizon was found
- next training command if a candidate is found
- any data/download issues

8. Optional training only if candidate exists

If the expanded horizon scan finds a recommended DUID/horizon pair, then run training only if the current train.py supports horizon labels.

If train.py does not support horizon labels yet, do NOT hack it.
Instead, record this next step:

Phase 2 must update train.py to train against the selected horizon label.

Final response format:
- Summary
- Files changed
- Tests
- Expanded scan result
- Best DUID/horizon candidate
- Next exact command
```

---

# 建议新增的 `phase1_5.md`

在项目根目录创建：

```bash
touch phase1_5.md
```

内容如下：

````md
# Phase 1.5 — Expanded Target Discovery and Forecast-Horizon Labels

## Objective

Phase 1 found that the current 5-minute `target_constrained` label is too sparse for a convincing ML demo. The scan found positive samples, but no DUID/window met the default recommendation thresholds.

Phase 1.5 expands the search and changes the target from "is constrained in this 5-minute interval" to "will be constrained within the next forecast horizon".

## Why This Phase Is Needed

The internship project is about forecasting when a DUID will experience a network-related constraint.

A direct 5-minute binding label is technically valid but often too sparse:

```text
target_constrained = 1 only when a relevant GENCONID is binding in the current dispatch interval
````

For a forecasting service, a better demo label is:

```text
target_constrained_next_1h = 1 if a relevant constraint binds within the next 1 hour
target_constrained_next_3h = 1 if a relevant constraint binds within the next 3 hours
target_constrained_next_6h = 1 if a relevant constraint binds within the next 6 hours
```

This is more useful for users and gives the ML model more positive samples.

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

* add a UI
* add deep learning
* build a production system
* fake scan results
* commit large raw AEMO data
* guess DUID coordinates
* optimise model performance before choosing a target

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
        ascending=[False, False, False]
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

The current interval may be excluded or included, but the choice must be documented. Preferred for forecasting:

```text
Use future intervals only, excluding the current interval.
```

## Leakage Rule

Features must not use future data.

Allowed features:

```text
current time
day of week
hour of day
current or historical dispatch data
current or historical weather
lag features
rolling historical features
```

Not allowed:

```text
future dispatch values
future weather observations
future constraint status as features
```

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

````

---

# 你现在实际要做的事

## 1. 先让 Codex 执行 Phase 1.5

不要现在手工改太多。把上面的 Codex prompt 发给它。

## 2. 等它生成这个报告

```text
chatref/phase1_5_horizon_scan_report.md
````

## 3. 如果找到推荐 DUID/horizon，下一步才是 Phase 2

Phase 2 的目标会是：

```text
用选出来的 DUID + horizon label 训练真正的 ML 模型
```

也就是把训练命令升级成类似：

```bash
python -m src.aemo_constraint_demo.train \
  --duid BULGANA1 \
  --lat <LAT> \
  --lon <LON> \
  --days 120 \
  --horizon-hours 3 \
  --model-out artifacts/bulgana1_3h.joblib
```

但现在不要猜经纬度。报告已经指出 coordinates 不能乱猜，需要先加 `data/duid_coordinates.csv`。
