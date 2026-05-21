## 结论：先不用重新设计“大模型架构”

如果目标是进一步提升预测效果，**第一优先级不是换成复杂模型**，而是：

```text
补高信号特征 > 做更可靠的 backtest > 再比较模型
```

你现在的模型已经不是 baseline，而是 `balanced_logistic_regression`，但它主要靠 **时间 + 天气特征**。当前结果显示：总体正样本率只有约 `1.32%`，chronological test split 没有正样本，所以 AP/AUC 是 `null`；positive-window backtest 虽然有 `precision_at_top_5_percent = 0.8`，但 `roc_auc = 0.3286`，说明排序能力还不稳定。

这不是“模型不够深”的典型问题，而是 **可解释信号不够**。README 也已经指出，当前 forecast features 仍然很 minimal，生产级版本应该加入 P5MIN / Predispatch、AEMO forecast demand、regional/interconnector forecasts、UIGF、outage schedules 等输入。

---

# 应该怎么提升预测效果

## 1. 不要优化普通 accuracy

这个任务是稀疏事件预测，`accuracy` 很容易骗人。

例如正样本率只有 `1.3%` 时，模型永远预测“不约束”，accuracy 也可能接近 `98.7%`，但业务上没有价值。

应该优化这些指标：

```text
average_precision
PR-AUC
precision_at_top_5_percent
precision_at_top_10_percent
lift_at_top_5_percent
recall_at_fixed_precision
calibration error
```

当前代码已经记录了 AP、ROC AUC、precision@top5%，这是正确方向。

---

## 2. 先加高信号 AEMO 特征

现在模型特征主要是：

```text
hour_sin
hour_cos
dow_sin
dow_cos
month_sin
month_cos
is_weekend
temperature
humidity
dew_point
wind_speed
wind_direction
shortwave_radiation
```

这些只能描述“时间和天气”，但网络约束通常更依赖：

```text
调度状态
约束 RHS/LHS margin
regional demand
interconnector flow
unit availability
outage / invoked constraint set
forecast dispatch / predispatch
```

所以准确率提升最大的方向是加特征。

### 推荐新增特征

| 特征类别                     | 具体特征                                                             | 为什么有用                                     |
| ------------------------ | ---------------------------------------------------------------- | ----------------------------------------- |
| **历史约束特征**               | 最近 1h/3h/6h 是否约束、约束次数、time since last constraint                 | 约束常常呈事件簇                                  |
| **constraint margin**    | `RHS - LHS`、`MARGINALVALUE` lag、`VIOLATIONDEGREE` lag            | 比单纯 binding label 信息量更高                   |
| **unit dispatch**        | `TOTALCLEARED`、`AVAILABILITY`、`INITIALMW`、ramp、semi-dispatch cap | DUID 出力状态会影响约束风险                          |
| **regional context**     | region demand、price、dispatch price、interconnector flow           | 约束通常受区域潮流和需求影响                            |
| **AEMO forecast inputs** | P5MIN / Predispatch constraint solution                          | 最贴近业务目标                                   |
| **weather forecast**     | forecast wind speed、gust、direction、temperature                   | 查询未来风险时必须用 forecast weather，而不是历史 weather |
| **constraint family**    | 按 `GENCONID` prefix / constraint type 分组                         | 不同约束机理不同，不应全部混成一个标签                       |

README 里已经说当前 planned/invoked view 只是 lightweight proxy，不是完整 P5MIN / Predispatch solution integration。这个是最值得做的性能提升点。

---

## 3. 再做模型 benchmark，而不是一上来换复杂架构

现在 `train.py` 里默认是：

```python
LogisticRegression(max_iter=2000, class_weight="balanced", random_state=42)
```

这是合理的 baseline。

下一步可以加一个模型比较器：

```text
logistic_regression
random_forest
hist_gradient_boosting
xgboost / lightgbm, 如果愿意加依赖
calibrated_gradient_boosting
```

推荐顺序：

## 第一档：不加新依赖

```text
balanced_logistic_regression
random_forest_balanced
hist_gradient_boosting
calibrated_logistic_regression
```

## 第二档：可以加依赖

```text
lightgbm
xgboost
catboost
```

对这个 tabular + sparse event 任务，**LightGBM / XGBoost 通常比深度学习更适合**。

---

# 要不要重新设计模型架构？

## 不需要立刻重写架构

现在的架构：

```text
AEMO data -> DUID mapping -> label frame -> feature engineering -> model -> bundle -> API
```

是对的。

你应该重构的是：

```text
feature pipeline
evaluation pipeline
model selection pipeline
```

而不是推倒重来。

---

## 什么时候才需要重新设计架构？

只有当你想从 demo 升级到更接近生产系统时，才需要重设架构。

### 当前 demo 架构

```text
one DUID -> one model
```

适合实习申请 demo。

### 更强版本架构

```text
multi-DUID shared model
+ DUID features
+ connection point features
+ region features
+ constraint family features
+ event-window backtesting
+ calibrated probability output
```

### 生产级架构

```text
feature store
+ AEMO P5MIN/Predispatch ingestion
+ outage schedule ingestion
+ multi-horizon forecasting
+ model registry
+ drift monitoring
+ retraining pipeline
```

所以答案是：

```text
短期：不用重新设计架构。
中期：加 feature pipeline + model benchmark。
长期：如果要多 DUID / 多约束类型 / 生产部署，才重新设计成 shared multi-DUID forecasting architecture。
```

---

# 我建议你的下一阶段：Phase 3 — Performance Improvement

目标不是“换模型”，而是做一个严谨的性能提升实验。

## Phase 3 目标

```text
证明哪些特征和模型能真正提升 CATHROCK 6h constraint risk ranking。
```

核心输出：

```text
artifacts/performance_benchmark.csv
artifacts/cathrock_6h_best_model.joblib
artifacts/cathrock_6h_feature_importance.csv
chatref/phase3_performance_report.md
```

---

# Phase 3 具体做法

## Step 1：固定 evaluation protocol

先不要动模型。先固定评估方式。

建议：

```text
event-window backtest
rolling-origin backtest
blocked time-series CV
```

每个 fold 必须：

```text
train time < validation time
```

不能随机切分。

---

## Step 2：加 feature sets

做 4 组 feature set，不要一次全加。

```text
A: time_only
B: time + weather
C: time + weather + dispatch_lag
D: time + weather + dispatch_lag + constraint_lag
E: time + weather + dispatch_lag + constraint_lag + planned/P5MIN proxy
```

你要看的是：

```text
feature set D 是否明显优于 B
```

如果 D 有明显提升，说明问题主要是特征，不是模型。

---

## Step 3：做模型 benchmark

每个 feature set 跑：

```text
logistic_regression
random_forest
hist_gradient_boosting
```

如果可以加依赖，再跑：

```text
lightgbm
xgboost
```

输出表格：

```text
model
feature_set
mean_average_precision
mean_precision_at_top_5_percent
mean_lift_at_top_5_percent
mean_roc_auc
evaluated_folds
skipped_folds
```

---

## Step 4：加 probability calibration

constraint risk service 最终输出的是概率，所以要做校准。

可以加：

```text
CalibratedClassifierCV
isotonic calibration
platt scaling
```

但注意：校准要用时间切分，不能随机切分。

---

## Step 5：做 feature importance

如果用 tree model，要输出：

```text
feature_importance
permutation_importance
```

如果用 logistic regression，要输出：

```text
coefficient table
```

这对 Proa Analytics 很有价值，因为它说明你不只是调模型，还能解释驱动因素。

---

# 给 Codex Lead 的 Phase 3 提示词

可以直接复制：

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Current status:
The project has a working CATHROCK 6h horizon demo:
- DUID: CATHROCK
- horizon_hours: 6
- model_type: balanced_logistic_regression
- target_column: target_constrained_next_6h
- total_samples: 17208
- positive_rate: about 0.01325
- chronological test split has no positive labels, so AP/AUC are null
- supplemental positive-window backtest has AP around 0.4726, ROC AUC around 0.3286, precision@top5 around 0.8

Current limitation:
The model mostly uses time and weather features. This is likely the main performance bottleneck.
Do not jump directly to deep learning.
Do not fake metrics.
Do not use random train/test splits.
Do not use future information as features.
Do not commit large raw AEMO data.

Mission:
Implement Phase 3: performance improvement through feature engineering, model benchmarking, and honest time-based evaluation.

Goal:
Improve ranking quality for CATHROCK 6h constraint-risk prediction and report whether improvements are real.

Required tasks:

1. Create feature set framework

Add or update:
src/aemo_constraint_demo/features.py
src/aemo_constraint_demo/train.py

Support named feature sets:

- time_only
- time_weather
- time_weather_dispatch_lag
- time_weather_constraint_lag
- time_weather_dispatch_constraint_lag

Add CLI argument:
--feature-set

Default:
time_weather

Feature rules:
- Features may use current or past information only.
- Do not include target columns.
- Do not include horizon-label columns.
- Do not include future observed dispatch or future constraints.

Suggested new features:
- lagged target_constrained over 1h, 3h, 6h
- rolling count of target_constrained over 1h, 3h, 6h
- time since last relevant constraint
- lagged unit dispatch fields where available
- rolling mean of unit dispatch fields
- lagged MARGINALVALUE or VIOLATIONDEGREE if available
- relevant GENCONID family counts

2. Add model choices

Update training to support:

--model-type logistic_regression
--model-type random_forest
--model-type hist_gradient_boosting

Optional only if dependencies are acceptable:
--model-type lightgbm
--model-type xgboost

Keep logistic_regression as default.

3. Add benchmark script

Create:

scripts/benchmark_models.py

Required command:

python scripts/benchmark_models.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --feature-sets time_only time_weather time_weather_dispatch_lag time_weather_constraint_lag \
  --model-types logistic_regression random_forest hist_gradient_boosting \
  --out artifacts/performance_benchmark.csv \
  --best-model-out artifacts/cathrock_6h_best_model.joblib \
  --report-out chatref/phase3_performance_report.md

Benchmark protocol:
- Use event-window or rolling-origin folds.
- Training rows must be strictly before validation rows.
- Skip invalid folds, but record skip reason.
- Do not random split time-series data.

Metrics:
- average_precision
- roc_auc
- precision_at_top_5_percent
- precision_at_top_10_percent
- lift_at_top_5_percent
- lift_at_top_10_percent
- calibration_brier_score if feasible
- evaluated_folds
- skipped_folds

4. Add feature importance

For the best model:
- If linear model: write coefficient table.
- If tree model: write feature importance table.
- Also compute permutation importance if feasible.

Output:
artifacts/cathrock_6h_feature_importance.csv

5. Update API to use best model

If benchmark finds a clearly better model, update README examples to use:

artifacts/cathrock_6h_best_model.joblib

Do not delete the old logistic model.

6. Update README

Add a section:

Phase 3: Performance Improvement

Include:
- benchmark command
- benchmark table summary
- best model
- best feature set
- honest metrics
- limitations
- why feature engineering helped or did not help

7. Tests

Add tests for:
- feature sets do not include target/horizon columns
- lag features use only past rows
- benchmark script writes expected CSV schema
- invalid folds are skipped not crashed
- model type selection works
- best model bundle includes feature_set and model_type

Run:
python -m pytest -q

8. Execution report

Create:

chatref/phase3_performance_report.md

Report must include:
- timestamp
- files changed
- commands run
- test result
- benchmark command
- evaluated feature sets
- evaluated model types
- best model
- best feature set
- best AP
- best precision@top5
- whether performance improved over Phase 2
- feature importance summary
- remaining limitations

Final response format:
- Summary
- Files changed
- Tests
- Benchmark result
- Best model
- Best feature set
- Whether performance improved
- Next recommended action
```

---

# 最现实的提升路线

我建议按这个顺序做：

## 第一轮：不换模型，只加 lag 特征

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --feature-set time_weather_constraint_lag \
  --model-type logistic_regression \
  --model-out artifacts/cathrock_6h_logreg_lag.joblib
```

看是否比现在的 AP / precision@top5 更好。

---

## 第二轮：加 tree model

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --feature-set time_weather_constraint_lag \
  --model-type hist_gradient_boosting \
  --model-out artifacts/cathrock_6h_hgb_lag.joblib
```

---

## 第三轮：加 P5MIN / Predispatch

这是最接近业务准确率提升的部分：

```text
AEMO planned constraints + ML forecast
```

如果能把 P5MIN / Predispatch constraint solution 接进去，模型会从“用天气猜约束”变成“用 AEMO 计划/预测信号 + ML 排序”。

这个比换深度学习更有价值。

---

# 最后建议

对于实习申请 demo，**不要把重点放在“我用了复杂模型”**。

更好的叙事是：

```text
I started with an interpretable baseline, found that time/weather features alone were insufficient, then improved the forecasting pipeline by adding grid-aware AEMO features, time-based backtesting, and model benchmarking.
```

这比单纯说“我换成 LSTM/Transformer”更符合 Proa Analytics 这类 energy analytics 项目的期待。
