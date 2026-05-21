我看了你上传的 final zip。结论是：**项目主体已经可以收尾，不建议继续加模型功能。接下来只剩 3 件事：修一个 API 鲁棒性小问题、整理 Git、准备投递材料。**

---

# 目前项目状态

你的 demo 已经具备完整故事线：

```text
AEMO public data
→ DUID target discovery
→ CATHROCK selection
→ future-only 6h horizon label
→ feature engineering
→ model benchmark
→ best model artifact
→ FastAPI query service
→ README / notebook / application materials
```

Phase 3 的最佳模型已经明确：

```text
DUID: CATHROCK
horizon: 6h
model: logistic_regression
feature_set: time_weather_dispatch_lag
mean AP: 0.615064
precision@top 5%: 0.633333
lift@top 5%: 1.944939
ROC AUC: 0.618640
```

Phase 3 报告也说明测试已通过，最佳设置是 `logistic_regression + time_weather_dispatch_lag`，并且结果仍被诚实定位为 sparse-fold evidence，不是 production accuracy。

README 顶部现在也已经能 60 秒内讲清楚 demo：选中 `CATHROCK`、`6h` horizon、最佳模型、关键指标，以及“不是生产级 forecast model”的限制说明。

---

# 但我发现一个需要优先处理的小问题

## API 在无网络环境下会失败

我本地检查 final zip 时，API 已经正确支持：

```text
horizon_hours omitted → 从 model bundle 推断 6
horizon_hours mismatch → 返回 400
```

这部分是好的。

但是我实际调用：

```bash
GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib
```

时，服务会尝试实时访问 Open-Meteo forecast API。如果机器没有网络，或者 Open-Meteo 暂时不可用，API 会直接抛异常，而不是返回一个 graceful fallback。

这对 GitHub demo 有风险。招聘方本地测试时，如果网络、防火墙、API 临时不可用，可能会以为项目坏了。

所以接下来最值得做的是：

# Phase 4.5：Offline-safe API Demo Patch

这不是新功能，是最终交付前的稳健性修补。

---

# 下一步具体做什么

## Step 1：让 API 在天气 API 失败时仍能返回 forecast

现在的 `forecast_from_bundle()` 会在 query 时调用 Open-Meteo forecast。建议改成：

```text
如果 Open-Meteo 成功：
    使用 live forecast weather

如果 Open-Meteo 失败：
    记录 weather_source = "fallback_imputed"
    weather feature 留空 / 使用 NaN
    交给模型 pipeline 的 SimpleImputer 处理
    API 仍返回 forecast
```

返回 JSON 里加：

```json
{
  "weather_source": "live_open_meteo"
}
```

或者：

```json
{
  "weather_source": "fallback_imputed",
  "weather_warning": "Open-Meteo forecast unavailable; weather features were imputed."
}
```

这样 demo 更稳。

---

## Step 2：加一个 API 参数

建议加：

```text
use_live_weather=true
```

例如：

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false"
```

当：

```text
use_live_weather=false
```

时，不访问 Open-Meteo，直接使用 imputed weather features。

这样 README 可以给两个命令：

### 在线版

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib"
```

### 离线安全版

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false"
```

---

## Step 3：补测试

新增或更新：

```text
tests/test_api.py
tests/test_forecast.py
```

测试至少包括：

```text
1. use_live_weather=false 时不调用 Open-Meteo
2. Open-Meteo 请求失败时 API 仍返回 200
3. response 包含 weather_source
4. horizon_hours omitted 时仍推断为 6
5. horizon_hours=24 mismatch 仍返回 400
```

---

# 给 Codex Lead 的最终修补提示词

直接复制给 Codex：

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Current status:
The final Phase 4 package is nearly ready for GitHub and Proa Analytics application use.

The project already has:
- CATHROCK 6h best model artifact
- README final summary
- notebook final story
- application materials
- tests passing in the Phase 4 report
- API horizon inference implemented
- API horizon mismatch returns 400

Important issue found during review:
The API forecast route still calls Open-Meteo live forecast at query time. If Open-Meteo is unavailable or the reviewer is offline, `/forecast/CATHROCK` can fail even though the model bundle and imputer are sufficient to produce a demo forecast.

Mission:
Implement Phase 4.5: offline-safe API forecast fallback.

Do not add new modelling work.
Do not add UI.
Do not add database.
Do not retrain models unless absolutely required.
Do not fake metrics.
Focus only on API robustness, README command clarity, tests, and final report.

Required tasks:

1. Update forecast API weather handling

Modify:
src/aemo_constraint_demo/forecast.py

Current behavior:
- forecast_from_bundle calls fetch_open_meteo_forecast when bundle no_weather is false.
- If the weather request fails, the API can crash.

Required behavior:
- Add parameter use_live_weather: bool = True to forecast_from_bundle.
- If use_live_weather is false, do not call Open-Meteo.
- If use_live_weather is true but Open-Meteo fails, catch the exception and continue.
- Missing weather columns should be filled with NA and passed through the model pipeline imputer.
- Return metadata fields:
  - weather_source
  - weather_warning

Suggested values:
- weather_source = "live_open_meteo" when live weather succeeds
- weather_source = "fallback_imputed" when live weather is disabled or unavailable
- weather_warning = null when live weather succeeds
- weather_warning = "Open-Meteo forecast unavailable; weather features were imputed." when fallback is used

2. Update FastAPI route

Modify:
app.py

Add query parameter:
use_live_weather: bool = Query(default=True)

Route should support:

GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib

and offline-safe:

GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false

Keep existing behavior:
- omitted horizon_hours is inferred from model bundle
- explicit matching horizon works
- explicit mismatched horizon returns 400

3. Refresh example response

Regenerate:
artifacts/example_cathrock_6h_response.json

Use:
model_path=artifacts/cathrock_6h_best_model.joblib
duid=CATHROCK
start=2026-05-20 00:00:00
use_live_weather=false

The example response should include:
- horizon_hours = 6
- model_type = logistic_regression
- feature_set = time_weather_dispatch_lag
- weather_source = fallback_imputed
- forecast rows > 0

4. Update README

Add both commands:

Online forecast:

curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib"

Offline-safe demo forecast:

curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false"

Explain briefly:
- live weather is used when available
- if live weather is unavailable, the demo can still run by imputing weather features
- this keeps the GitHub demo reproducible even without internet access

5. Add tests

Update:
tests/test_forecast.py
tests/test_api.py

Minimum tests:
- forecast_from_bundle(use_live_weather=False) does not call Open-Meteo and returns forecast rows
- forecast_from_bundle catches mocked Open-Meteo failure and returns fallback_imputed
- API route accepts use_live_weather=false
- API response includes weather_source
- API horizon inference still works
- API horizon mismatch still returns 400

Run:
python -m pytest -q

6. Update final report

Create:
chatref/phase4_5_offline_api_report.md

Report must include:
- timestamp
- files changed
- tests result
- online API command
- offline-safe API command
- whether Open-Meteo failure fallback was tested
- whether README was updated
- whether example response was refreshed
- remaining limitations
- ready for GitHub yes/no

Final response format:
- Summary
- Files changed
- Tests
- API fallback result
- README/example response status
- Ready for GitHub
- Next git commands
```

---

# Step 4：处理 Git 状态

Phase 4 报告里已经指出还有一些 pre-existing 状态：

```text
deleted: chatref/phase0/codex_execute_report.md
deleted: phase1.md
deleted: phase1_5.md
untracked: chatref/codex_execute_report.md
untracked: doc/phase3.md
untracked: doc/phase4.md
```

这一步很重要：**不要直接 `git add .`。**

建议先执行：

```bash
git status
```

如果这些删除不是你故意的，先恢复：

```bash
git restore -- phase1.md phase1_5.md chatref/phase0/codex_execute_report.md
```

然后只 stage 需要提交的文件。

---

# Step 5：最终提交

等 Phase 4.5 修完后，推荐提交：

```bash
git add \
  .gitignore \
  README.md \
  requirements.txt \
  app.py \
  src/aemo_constraint_demo/forecast.py \
  tests/test_api.py \
  tests/test_forecast.py \
  notebooks/aemo_constraint_forecast_demo.ipynb \
  artifacts/example_cathrock_6h_response.json \
  artifacts/cathrock_6h_best_model.joblib \
  application/project_summary.md \
  application/resume_bullets.md \
  application/interview_talking_points.md \
  plan/phase4_final_polish.md \
  chatref/phase4_final_polish_report.md \
  chatref/phase4_5_offline_api_report.md
```

然后：

```bash
git diff --cached --stat
git commit -m "Polish AEMO constraint forecasting demo for application"
```

创建 GitHub repo：

```bash
gh repo create aemo-network-constraint-demo --public --source=. --remote=origin --push
```

---

# Step 6：投递前材料准备

你的 `application/` 目录已经有：

```text
application/project_summary.md
application/resume_bullets.md
application/interview_talking_points.md
```

这些就是下一步申请材料的基础。

投递前建议再做三个小件：

## 1. GitHub README 开头的一句话

例如：

```text
A compact AEMO public-data ML demo that maps a NEM DUID to relevant generic constraints, builds future-only 6h sparse-event labels, benchmarks leakage-guarded models, and exposes constraint-risk forecasts through FastAPI.
```

## 2. 简历 bullet

可以用：

```text
Built a GitHub-ready FastAPI demo forecasting AEMO NEM constraint risk for a selected DUID using public NEMWeb dispatch/constraint data, future-only 6h labels, and leakage-guarded time/weather/dispatch-lag features.
```

## 3. 面试讲法

核心讲法：

```text
The hardest part was not the classifier itself. It was turning sparse AEMO constraint data into a valid supervised learning problem. I first found that the initial DUID had no positives, built a target discovery scan, selected CATHROCK, created a future-only 6h horizon label, and used event-window backtests to avoid overstating performance.
```

---

# 总结：接下来只做这个

优先级如下：

| 优先级 | 任务                                               | 是否必须 |
| --- | ------------------------------------------------ | ---- |
| 1   | 加 `use_live_weather=false` / Open-Meteo fallback | 必须   |
| 2   | 更新 README 离线安全 API 命令                            | 必须   |
| 3   | 刷新 example response                              | 必须   |
| 4   | 处理 git status，不要 `git add .`                     | 必须   |
| 5   | push GitHub                                      | 必须   |
| 6   | 用 application materials 准备投递                     | 必须   |
| 7   | 继续加模型 / P5MIN / UI                               | 暂时不要 |

你现在把 **Phase 4.5 prompt** 发给 Codex。跑完后上传：

```text
chatref/phase4_5_offline_api_report.md
README.md
app.py
src/aemo_constraint_demo/forecast.py
tests/test_api.py
tests/test_forecast.py
artifacts/example_cathrock_6h_response.json
```

如果 Phase 4.5 通过，这个项目就可以 push GitHub 并用于申请。
