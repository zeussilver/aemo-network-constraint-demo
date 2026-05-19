## 当前判断

现在应该进入 **Phase 2：Horizon-based ML Training + API Demo Polish**。

原因很明确：Phase 1.5 已经找到可训练目标。报告显示扩大扫描产生了 `1800` 行结果，`105` 行有正样本，`16` 行达到推荐标准；最佳候选是：

```text
DUID: CATHROCK
days: 60
horizon_hours: 6
connection_point_id: SCRK
positive_intervals: 228
positive_rate: 0.013194
```

这已经满足 Phase 1.5 的推荐条件。测试也通过：`38 passed`。

所以现在不要继续做 target discovery。下一步是把训练脚本从“当前 5 分钟是否约束”升级成：

```text
未来 6 小时内，CATHROCK 是否会遇到相关网络约束
```

---

# 下一步怎么做

## Phase 2 目标

让 repo 能真正训练一个非 baseline 的 ML 模型，并通过 API 查询。

核心任务：

```text
CATHROCK + 60 days + 6h horizon
```

训练目标：

```text
target_constrained_next_6h
```

而不是旧的：

```text
target_constrained
```

---

## Phase 2 必须完成的事情

### 1. 给 `train.py` 增加 `--horizon-hours`

当前报告明确说：

```text
src/aemo_constraint_demo/train.py does not yet support --horizon-hours
```

所以 Phase 2 第一件事就是改 trainer。

目标命令应该变成：

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --lat <LAT> \
  --lon <LON> \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib
```

---

### 2. 加入 vetted 坐标文件

不要猜 CATHROCK 经纬度。报告也明确提醒：coordinates should not be guessed。

建议新增：

```text
data/duid_coordinates.csv
```

字段：

```csv
duid,lat,lon,source,notes
CATHROCK,<LAT>,<LON>,<SOURCE>,<NOTES>
```

如果暂时没有可靠坐标，Phase 2 可以先支持：

```text
--lat / --lon 手动传入
```

但最终 GitHub demo 里最好有一个小型 coordinate mapping 文件。

---

### 3. 训练真正的模型

训练完成后，模型不能还是：

```text
constant_probability_baseline
```

至少要能训练：

```text
LogisticRegression
RandomForestClassifier
GradientBoostingClassifier
```

建议别上复杂模型，优先用：

```text
LogisticRegression(class_weight="balanced")
```

或者：

```text
RandomForestClassifier(class_weight="balanced")
```

Phase 2 重点不是刷分，而是证明流程完整：

```text
DUID -> constraints -> horizon labels -> weather/time features -> ML model -> API query
```

---

### 4. 输出 metrics

训练后必须生成：

```text
artifacts/cathrock_6h_metrics.json
```

至少包含：

```json
{
  "duid": "CATHROCK",
  "days": 60,
  "horizon_hours": 6,
  "model_type": "...",
  "total_samples": 0,
  "train_samples": 0,
  "test_samples": 0,
  "positive_rate": 0.0,
  "train_positive_rate": 0.0,
  "test_positive_rate": 0.0,
  "average_precision": 0.0,
  "roc_auc": 0.0,
  "precision_at_top_5_percent": 0.0,
  "features": []
}
```

如果 `roc_auc` 因测试集单类而无法计算，要如实写 `null`，不能伪造。

---

### 5. 更新 API

现在 API 应该支持查询：

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib"
```

返回结构建议：

```json
{
  "duid": "CATHROCK",
  "horizon_hours": 6,
  "model": {
    "model_type": "...",
    "positive_rate": 0.013194
  },
  "forecast": [
    {
      "interval_start": "2026-05-20T00:00:00",
      "probability": 0.18,
      "risk_level": "medium"
    }
  ],
  "planned_constraints": [],
  "relevant_genconids": []
}
```

Demo 不需要复杂，但必须能说明：

```text
输入 DUID，返回 planned constraints + ML forecast
```

---

# 给 Codex Lead 的 Phase 2 提示词

直接复制给 Codex：

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Current status:
Phase 1.5 succeeded. Expanded horizon scan found recommended DUID/horizon candidates.
Best candidate:
- duid: CATHROCK
- days: 60
- horizon_hours: 6
- connection_point_id: SCRK
- relevant_genconid_count: 103
- positive_intervals: 228
- positive_rate: 0.013194
- unique_binding_genconid_count: 3

Tests currently pass:
- 38 passed

Important:
src/aemo_constraint_demo/train.py does not yet support --horizon-hours.
Do not proceed with old 5-minute target training.
Do not train another constant_probability_baseline unless the selected horizon label unexpectedly has one class.
Do not fake metrics.
Do not guess coordinates.
Do not commit large raw AEMO data.
Do not add a UI.
Keep this a clean internship demo.

Mission:
Implement Phase 2: horizon-based ML training and API demo polish.

Required tasks:

1. Update trainer

Modify:
src/aemo_constraint_demo/train.py

Add CLI argument:
--horizon-hours

Expected command:

python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --lat <LAT> \
  --lon <LON> \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib

Training target:
target_constrained_next_6h

Rules:
- Use future-only horizon labels.
- Exclude current interval from the horizon label unless already documented otherwise.
- Avoid feature leakage.
- Features may use current/past time, current/past dispatch data, and weather/time features.
- Features must not use future constraints or future observed dispatch data.

2. Add coordinate handling

Add:
data/duid_coordinates.csv

Schema:
duid,lat,lon,source,notes

Add CATHROCK only if coordinates are verified from a reliable source.
If coordinates cannot be verified, do not invent them.
If coordinates are missing, allow --lat and --lon to be passed manually.

3. Train selected model

After implementation, run training for CATHROCK 60-day 6-hour horizon.

If verified coordinates are available:

python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib

If coordinates are only passed manually:

python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --lat <LAT> \
  --lon <LON> \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib

4. Metrics

Write:
artifacts/cathrock_6h_metrics.json

Must include:
- duid
- days
- horizon_hours
- connection_point_id
- relevant_genconid_count
- model_type
- total_samples
- train_samples
- test_samples
- positive_rate
- train_positive_rate
- test_positive_rate
- average_precision
- roc_auc
- precision_at_top_5_percent
- features
- trained_at

If the model falls back to constant_probability_baseline, explain why in the report.

5. API update

Update:
app.py

The API should support:
GET /forecast/{duid}?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib

Response should include:
- duid
- horizon_hours
- model metadata
- relevant_genconids or count
- planned constraints if available
- ML forecast probabilities
- risk levels

Also create:
artifacts/example_cathrock_6h_response.json

6. Tests

Update or add tests:
tests/test_train.py
tests/test_api.py
tests/test_scan.py if needed

Minimum tests:
- --horizon-hours is accepted by train CLI
- selected horizon label is used
- leakage-prone future columns are not used as features
- metrics JSON includes horizon_hours
- API response includes horizon_hours
- API can load horizon-trained model artifact or mocked artifact

Run:
python -m pytest -q

7. README

Update README with:
- selected Phase 2 target: CATHROCK, 60 days, 6h horizon
- training command
- API query command
- explanation of horizon label
- metrics summary
- limitations

8. Execution report

Create:
chatref/phase2_horizon_training_report.md

Report must include:
- timestamp
- files changed
- commands run
- test result
- training command
- model artifact path
- metrics artifact path
- model type
- positive rate
- average precision
- ROC AUC
- API command tested
- example response path
- issues encountered
- whether demo is ready for GitHub polish

Final response format:
- Summary
- Files changed
- Tests
- Training result
- API result
- Whether the model is non-baseline
- Remaining issues
- Next command
```

---

# 你需要给我什么文件来判断

Phase 2 跑完后，最好直接给我整个 repo zip。最省事。

## 最推荐上传

在项目上一级目录执行：

```bash
cd /Users/liuzhenqian/Desktop

zip -r aemo-network-constraint-demo-phase2.zip aemo-network-constraint-demo \
  -x "*/.venv/*" \
  -x "*/__pycache__/*" \
  -x "*/.pytest_cache/*" \
  -x "*/data/cache/*" \
  -x "*/.git/*"
```

然后把这个文件上传给我：

```text
aemo-network-constraint-demo-phase2.zip
```

这样我可以完整判断代码、README、报告、metrics、API 示例是否一致。

---

## 如果不想传整个 repo，至少给我这些文件

### 必须给

```text
chatref/phase2_horizon_training_report.md
artifacts/cathrock_6h_metrics.json
artifacts/example_cathrock_6h_response.json
src/aemo_constraint_demo/train.py
src/aemo_constraint_demo/scan.py
app.py
README.md
```

### 强烈建议给

```text
data/duid_coordinates.csv
tests/test_train.py
tests/test_api.py
tests/test_scan.py
artifacts/duid_constraint_scan_expanded_top.csv
```

### 不需要给

```text
.venv/
__pycache__/
.pytest_cache/
大型 NEMWeb 原始下载文件
大型 cache 文件
```

---

# 我会怎么判断 Phase 2 是否合格

我会重点看 7 件事：

## 1. 是否真的用了 horizon label

必须看到：

```text
horizon_hours = 6
target_constrained_next_6h
```

而不是继续用旧的：

```text
target_constrained
```

---

## 2. 是否避免数据泄漏

不能把这些东西作为 feature：

```text
target_constrained
target_constrained_next_1h
target_constrained_next_3h
target_constrained_next_6h
future constraint status
future dispatch outcome
```

---

## 3. 模型是否不再是常数 baseline

理想结果：

```text
model_type != constant_probability_baseline
```

如果仍然 fallback，说明训练切分或标签构造有问题，需要继续修。

---

## 4. metrics 是否可信

我会看：

```text
positive_rate
train_positive_rate
test_positive_rate
average_precision
roc_auc
precision_at_top_5_percent
```

不要只看 accuracy。这个任务是稀疏事件预测，accuracy 没什么意义。

---

## 5. API 是否能查询

必须能用类似命令查询：

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib"
```

并返回：

```text
DUID
horizon_hours
probability
risk_level
planned constraints / relevant constraints 信息
```

---

## 6. README 是否能让招聘方复现

README 至少要有：

```text
why this target was selected
how labels are defined
how to train
how to query API
what the result means
known limitations
```

---

## 7. GitHub demo 是否足够清晰

最终目标不是做生产系统，而是让 Proa Analytics 看到：

```text
你理解 NEM / AEMO 数据
你能把模糊业务问题变成标签定义
你能处理稀疏事件预测
你能做可查询服务
你能诚实报告模型局限
```

---

# 当前最短执行路径

你现在给 Codex 上面的 Phase 2 prompt。
Codex 跑完后，把这个文件上传给我：

```text
aemo-network-constraint-demo-phase2.zip
```

或者至少上传：

```text
chatref/phase2_horizon_training_report.md
artifacts/cathrock_6h_metrics.json
artifacts/example_cathrock_6h_response.json
README.md
src/aemo_constraint_demo/train.py
app.py
```
