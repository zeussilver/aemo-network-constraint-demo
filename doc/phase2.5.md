## 当前状态判断

Phase 2 已经达到 **“可运行 demo”** 标准，但还没有达到 **“适合直接发给 Proa Analytics 的 polished GitHub demo”** 标准。

已完成的好消息：

* `CATHROCK + 60 days + 6h horizon` 已经训练成功。
* 模型不是 baseline，而是 `balanced_logistic_regression`。
* 测试通过：`41 passed`。
* API smoke test 成功返回 `200`，并输出 `72` 条 forecast rows。
* API 示例 response 已生成。

但还有一个关键弱点：

```text
chronological test split 的 test_positive_rate = 0.0
average_precision = null
roc_auc = null
```

也就是说，当前主测试集没有正样本，所以主指标不能证明模型有效。虽然 supplemental positive backtest 有结果，例如 `average_precision = 0.4726`、`precision_at_top_5_percent = 0.8`，但 `roc_auc = 0.3286` 偏弱，不能包装成强模型。

所以，下一步不是继续扩大功能，也不是上复杂模型，而是做：

# Phase 2.5：Evaluation Hardening + Demo Polish

目标是把当前 demo 从：

```text
能跑
```

提升到：

```text
招聘方看了觉得方法诚实、结构清楚、结果可信、可复现
```

---

# 下一步具体做什么

## 1. 加一个更可信的 backtest 评估脚本

现在主测试集没有正样本，`average_precision` 和 `roc_auc` 都是 `null`。这个结果是诚实的，但单独放在 README 里会显得模型没有评估价值。

下一步应该新增一个轻量 backtest：

```text
scripts/backtest_horizon_model.py
```

它要做的事：

```text
找到 CATHROCK 历史正样本事件窗口
对每个窗口，只用该窗口之前的数据训练
在该窗口附近做验证
输出 AP / ROC AUC / precision@top5% / lift@top5%
```

这比现在的单个 supplemental backtest 更有说服力，因为它不是只挑一个窗口，而是系统性地评估多个 positive event windows。

建议输出：

```text
artifacts/cathrock_6h_backtest_summary.json
artifacts/cathrock_6h_backtest_folds.csv
```

---

## 2. 修 API 默认 horizon 的小问题

当前 `app.py` 里：

```python
horizon_hours: int = Query(default=24, ge=1, le=72)
```

但 Phase 2 模型是 `6h horizon`。

这会导致一个小的 demo friction：

```bash
/forecast/CATHROCK
```

默认会走 `24h`，但你的模型 artifact 是 `6h`。现在 README 里当然写了要传 `horizon_hours=6`，但招聘方直接试 API 时容易踩坑。

建议改成：

```text
如果用户没有传 horizon_hours，则从 model bundle 里读取 horizon_hours
```

或者更简单：

```text
默认 horizon_hours 改成 6
```

我更推荐第一种，显得更工程化。

---

## 3. 更新 notebook，让它匹配 Phase 2

最开始的 notebook 很可能还是 Phase 0/Phase 1 风格。现在 GitHub demo 的 notebook 应该围绕：

```text
CATHROCK
60 days
6h horizon
target_constrained_next_6h
balanced_logistic_regression
API query example
```

Notebook 不需要重新做大工程，但必须展示完整故事：

```text
1. 为什么先做 target discovery
2. 为什么选择 CATHROCK
3. 如何定义 horizon label
4. 如何训练模型
5. 如何解释 metrics
6. 如何调用 API
```

这对实习申请非常重要。很多 reviewer 不会先读所有源码，会先看 README 和 notebook。

---

## 4. README 要改成“诚实但有说服力”的叙事

README 现在已经写了 Phase 2 的训练命令和 label 定义，这是好的。

但下一步需要把结果表达得更清楚：

### 应该强调

```text
This is a forecasting workflow demo, not a production-accurate model.
```

### 应该明确说

```text
The normal chronological holdout contains no positive CATHROCK horizon events, so AP/AUC are null.
To still evaluate ranking behaviour around rare events, we include event-window backtests trained only on prior data.
```

### 不要说

```text
The model predicts constraints accurately.
```

当前结果还不支持这个说法。

---

## 5. 做 final GitHub packaging

等 Phase 2.5 完成后，才进入最终打包：

```text
Phase 3: GitHub release polish
```

Phase 3 只做这些：

```text
README cleanup
notebook cleanup
requirements check
smoke test command
screenshots or sample curl response
final zip / GitHub push
```

不要再加新模型、新 UI、新数据库。

---

# 给 Codex Lead 的 Phase 2.5 提示词

直接复制下面这段给 Codex。

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Current status:
Phase 2 succeeded in producing a runnable horizon-based ML demo.

Known Phase 2 result:
- DUID: CATHROCK
- days: 60
- horizon_hours: 6
- target_column: target_constrained_next_6h
- connection_point_id: SCRK
- relevant_genconid_count: 103
- model_type: balanced_logistic_regression
- non-baseline model: yes
- tests: 41 passed
- API smoke test returned 200 and 72 forecast rows

Important limitation:
The main chronological test split has no positive labels:
- test_positive_rate = 0.0
- average_precision = null
- roc_auc = null

There is a supplemental positive-window backtest:
- average_precision around 0.4726
- roc_auc around 0.3286
- precision_at_top_5_percent around 0.8

Do not overstate model performance.
Do not fake metrics.
Do not add deep learning.
Do not add a UI.
Do not add a database.
Do not commit large raw AEMO data.
Keep this as a clean internship demo.

Mission:
Implement Phase 2.5: evaluation hardening and demo polish.

Goal:
Make the demo credible for a GitHub internship application by adding a transparent event-window backtest, fixing API demo friction, and updating README/notebook around the selected CATHROCK 6h model.

Required tasks:

1. Add event-window backtest script

Create:

scripts/backtest_horizon_model.py

The script should evaluate CATHROCK 60-day 6h horizon using event-window backtesting.

Required command:

python scripts/backtest_horizon_model.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib \
  --out artifacts/cathrock_6h_backtest_summary.json \
  --folds-out artifacts/cathrock_6h_backtest_folds.csv \
  --max-folds 5

Backtest rules:
- Identify positive target windows from target_constrained_next_6h.
- For each validation window, train only on rows strictly before the validation window.
- Do not train on future rows.
- Skip folds where prior training data has only one target class.
- Skip folds where validation data has only one target class, but record the reason.
- Use the same features as train.py.
- Do not use target columns or future-label columns as features.
- Use weather/time features consistently with train.py.

Metrics per fold:
- fold_id
- validation_start
- validation_end
- train_samples
- validation_samples
- train_positive_rate
- validation_positive_rate
- average_precision
- roc_auc
- precision_at_top_5_percent
- precision_at_top_10_percent
- lift_at_top_5_percent
- lift_at_top_10_percent
- model_type
- skipped
- skip_reason

Summary JSON:
- duid
- days
- horizon_hours
- target_column
- evaluated_folds
- skipped_folds
- mean_average_precision
- mean_roc_auc
- mean_precision_at_top_5_percent
- mean_precision_at_top_10_percent
- mean_lift_at_top_5_percent
- mean_lift_at_top_10_percent
- limitations
- generated_at

2. Fix API default horizon behaviour

Current app.py defaults horizon_hours to 24 even though the Phase 2 model is 6h.

Update the API so that:
- If horizon_hours is omitted, use the horizon_hours stored in the model bundle.
- If horizon_hours is provided and mismatches the model bundle, either:
  - return a clear 400 error, or
  - document exactly how forecast_from_bundle handles it.

Preferred:
- Make horizon_hours optional in the FastAPI route.
- Infer model horizon when omitted.

The endpoint should still support:

GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h.joblib

and:

GET /forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib

3. Generate improved API examples

Create:

artifacts/example_cathrock_6h_response.json
artifacts/example_cathrock_6h_backtest_response.json

The first can use a current/future-style timestamp.
The second should use a historical timestamp near a validation event to show non-trivial risk variation.

Clearly label the historical example as a backtest/demo example, not a real future forecast.

4. Update notebook

Update:

notebooks/aemo_constraint_forecast_demo.ipynb

The notebook should show:
- target discovery summary
- why CATHROCK was selected
- horizon label definition
- training command
- model metrics
- event-window backtest summary
- API query example
- limitations

Keep it compact and readable.
Do not make it a giant research notebook.

5. Update README

Update README with:
- Phase 2.5 section
- event-window backtest command
- explanation of why main chronological AP/AUC are null
- backtest summary
- API command with omitted horizon_hours
- API command with explicit horizon_hours=6
- limitations written honestly

Do not claim production accuracy.

6. Add tests

Add or update tests:

tests/test_backtest.py
tests/test_api.py
tests/test_train.py if needed

Minimum tests:
- event-window fold generation uses only prior training rows
- skipped folds are reported instead of crashing
- lift_at_top_k handles zero-positive validation safely
- API infers horizon_hours from model metadata when omitted
- API rejects or handles mismatched horizon_hours clearly
- no target or horizon-label columns are selected as features

Run:

python -m pytest -q

7. Create execution report

Create:

chatref/phase2_5_evaluation_report.md

Report must include:
- timestamp
- files changed
- commands run
- test result
- backtest command
- backtest output paths
- evaluated folds
- skipped folds
- mean AP
- mean ROC AUC
- mean precision@top5%
- mean lift@top5%
- API smoke test command
- whether API horizon inference works
- notebook updated yes/no
- README updated yes/no
- remaining limitations
- whether the repo is ready for final GitHub polish

Final response format:
- Summary
- Files changed
- Tests
- Backtest result
- API result
- README/notebook result
- Remaining issues
- Next command
```

---

# 建议新增 `phase2_5.md`

也让 Codex 创建这个文件：

````md
# Phase 2.5 — Evaluation Hardening and Demo Polish

## Objective

Phase 2 produced a working horizon-based ML model and API for `CATHROCK`, but the main chronological holdout set has no positive labels. Phase 2.5 adds transparent rare-event backtesting and improves the demo so it is credible for a GitHub internship application.

## Current Model

```text
DUID: CATHROCK
days: 60
horizon_hours: 6
target_column: target_constrained_next_6h
model_type: balanced_logistic_regression
````

## Main Limitation

The chronological test split contains no positive labels:

```text
test_positive_rate = 0.0
average_precision = null
roc_auc = null
```

This must be reported honestly.

## Goal

Add event-window backtesting where each validation fold is evaluated on a window containing positive labels, while training only on data before the validation window.

## Required Artifacts

```text
scripts/backtest_horizon_model.py
artifacts/cathrock_6h_backtest_summary.json
artifacts/cathrock_6h_backtest_folds.csv
artifacts/example_cathrock_6h_response.json
artifacts/example_cathrock_6h_backtest_response.json
chatref/phase2_5_evaluation_report.md
```

## Required Command

```bash
python scripts/backtest_horizon_model.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib \
  --out artifacts/cathrock_6h_backtest_summary.json \
  --folds-out artifacts/cathrock_6h_backtest_folds.csv \
  --max-folds 5
```

## Validation Rules

* Train only on rows before the validation window.
* Do not use future rows.
* Do not use target or horizon-label columns as features.
* Skip invalid folds, but record why.
* Prefer Average Precision, Precision@TopK, and Lift@TopK over accuracy.

## API Polish

The API should infer `horizon_hours` from the model bundle if the query parameter is omitted.

Supported:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h.joblib"
```

Also supported:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib"
```

## Definition of Done

Phase 2.5 is done when:

* `python -m pytest -q` passes.
* Event-window backtest outputs exist.
* API horizon inference works.
* README explains the null chronological metrics honestly.
* Notebook reflects the final CATHROCK 6h workflow.
* Execution report exists.

````

---

# Phase 2.5 完成后你给我什么

最推荐直接上传：

```text
aemo-network-constraint-demo-phase2_5.zip
````

打包命令：

```bash
cd /Users/liuzhenqian/Desktop

zip -r aemo-network-constraint-demo-phase2_5.zip aemo-network-constraint-demo \
  -x "*/.venv/*" \
  -x "*/__pycache__/*" \
  -x "*/.pytest_cache/*" \
  -x "*/data/cache/*" \
  -x "*/.git/*"
```

如果不传整个 repo，至少传这些：

```text
chatref/phase2_5_evaluation_report.md
artifacts/cathrock_6h_backtest_summary.json
artifacts/cathrock_6h_backtest_folds.csv
artifacts/example_cathrock_6h_response.json
artifacts/example_cathrock_6h_backtest_response.json
scripts/backtest_horizon_model.py
src/aemo_constraint_demo/train.py
src/aemo_constraint_demo/forecast.py
app.py
README.md
notebooks/aemo_constraint_forecast_demo.ipynb
```

---

# 最短路线

你现在给 Codex 执行 **Phase 2.5 prompt**。

如果 Phase 2.5 后：

```text
tests pass
backtest report exists
README clear
notebook updated
API horizon inference works
```

下一步就是最终阶段：

```text
Phase 3: GitHub final polish + application positioning
```

到 Phase 3 才考虑写申请用的项目简介、GitHub README 摘要、LinkedIn/简历 bullet points。
