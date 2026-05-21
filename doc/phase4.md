## 当前判断

现在应该进入 **Phase 4：Final GitHub Polish + Application Packaging**。

不要继续急着加模型了。Phase 3 已经证明了一个关键点：**增加 dispatch lag 特征比单纯换复杂模型更有效**。最佳结果是：

```text
model: logistic_regression
feature_set: time_weather_dispatch_lag
mean_average_precision: 0.615064
mean_precision_at_top_5_percent: 0.633333
mean_lift_at_top_5_percent: 1.944939
mean_roc_auc: 0.618640
evaluated_folds: 2
skipped_folds: 1
```

并且测试通过：`56 passed, 2 warnings`。

所以现在项目已经从：

```text
能跑的 demo
```

提升到了：

```text
有 target discovery、有 horizon label、有 backtest、有 feature benchmark、有 best model 的完整实习申请 demo
```

下一步是把它打磨成招聘方一打开 GitHub 就能看懂、复现、评价的版本。

---

# 下一步做什么

## Step 1：先做 repo 一致性检查

你现在有一个潜在不一致点，需要优先确认。

README 里写的是：

```text
GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib
```

并说明 API 会从 model bundle 推断 `horizon_hours=6`。

但你之前上传的 `app.py` 里还是：

```python
horizon_hours: int = Query(default=24, ge=1, le=72)
```

也就是默认 `24` 小时，不是自动推断。

所以第一件事是让 Codex 检查实际 repo 里的 `app.py` 和 `forecast.py`：

```text
如果实际 repo 已经修好了，就不用动。
如果还没修，必须修。
```

否则 README 和代码不一致，招聘方按 README 跑 API 可能会踩坑。

---

## Step 2：用 best model 重新生成 API example

Phase 3 的最佳模型已经变成：

```text
artifacts/cathrock_6h_best_model.joblib
```

不是旧的：

```text
artifacts/cathrock_6h.joblib
```

所以需要确认这些 artifact 都是基于 best model 生成的：

```text
artifacts/example_cathrock_6h_response.json
artifacts/cathrock_6h_best_model_metrics.json
artifacts/cathrock_6h_feature_importance.csv
artifacts/performance_benchmark.csv
```

如果 example response 还是旧模型生成的，要重新生成。

---

## Step 3：做 clean-room 复现测试

在一个干净目录里重新 clone 或复制 repo，然后跑 README 里的最短命令。

目标是确认招聘方按 README 能跑通：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
uvicorn app:app --reload
```

然后另一个终端跑：

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib"
```

如果这个命令不能直接返回 forecast，就必须修。

---

## Step 4：整理 Git 状态

Phase 3 执行计划里提到之前有一些 pre-existing uncommitted / untracked / deleted 状态，例如：

```text
deleted: chatref/phase0/codex_execute_report.md, phase1.md, phase1_5.md
untracked: chatref/codex_execute_report.md, doc/phase3.md
```

并且计划里也要求 review git status/diff 后，只 commit Phase 3-owned changes。

所以现在要做一次：

```bash
git status
git diff
```

目标：

```text
不要把临时文件、大 cache、无关删除、旧报告混乱地一起 commit。
```

---

## Step 5：最终 README / Notebook 检查

README 已经有 Phase 3 性能结果，并且诚实说明了限制：event-window folds 很少，chronological holdout 仍可能没有正样本，P5MIN/Predispatch、outage、regional demand、interconnector features 仍是未来扩展。

现在只需要做最终 polish：

### README 首页应该让招聘方 30 秒看懂

建议 README 顶部加一个很短的结果摘要：

```md
## Demo result

Selected target: CATHROCK, 6h horizon  
Best model: logistic regression with time + weather + dispatch-lag features  
Mean event-window AP: 0.615  
Mean precision@top 5%: 0.633  
Mean lift@top 5%: 1.945  

This is a workflow demo, not a production forecast model.
```

### Notebook 应该轻，不要太长

Notebook 只保留：

```text
problem framing
target discovery
CATHROCK selection
horizon label
training
benchmark result
API query
limitations
```

不要放大量中间 debugging 内容。

---

# 给 Codex Lead 的 Phase 4 提示词

直接复制下面这段给 Codex。

```text
You are the Codex Lead for this repository:

/Users/liuzhenqian/Desktop/aemo-network-constraint-demo

Current status:
Phase 3 is complete.
The best setting is:
- DUID: CATHROCK
- horizon_hours: 6
- model: logistic_regression
- feature_set: time_weather_dispatch_lag
- mean_average_precision: 0.615064
- mean_precision_at_top_5_percent: 0.633333
- mean_lift_at_top_5_percent: 1.944939
- mean_roc_auc: 0.618640
- tests: 56 passed, 2 warnings

Mission:
Implement Phase 4: final GitHub polish and application packaging.

Do not add new modelling complexity.
Do not add deep learning.
Do not add a UI.
Do not add a database.
Do not fake results.
Do not commit large raw AEMO data or cache files.
Focus on consistency, reproducibility, README clarity, notebook clarity, and final application materials.

Required tasks:

1. Repo consistency audit

Check the actual repository versions of:
- app.py
- src/aemo_constraint_demo/forecast.py
- README.md
- artifacts/example_cathrock_6h_response.json

The README says:
GET /forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib

and says the API infers horizon_hours=6 from the model bundle.

Verify this is actually true.

If app.py still has:
horizon_hours default 24

then fix it so horizon_hours is optional and inferred from the model bundle when omitted.

The API must support both:

curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib"

and:

curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h_best_model.joblib"

If explicit horizon_hours mismatches the model bundle, return a clear 400 error.

2. Regenerate best-model API example

Use:
artifacts/cathrock_6h_best_model.joblib

Generate or refresh:
artifacts/example_cathrock_6h_response.json

The response must come from the best model, not the older artifacts/cathrock_6h.joblib model.

3. Final README polish

Update README so the first 60 seconds communicate:
- what the demo does
- why it maps to the Proa Analytics internship brief
- selected target: CATHROCK
- horizon: 6h
- best model: logistic_regression with time_weather_dispatch_lag
- key benchmark metrics:
  - mean_average_precision: 0.615064
  - mean_precision_at_top_5_percent: 0.633333
  - mean_lift_at_top_5_percent: 1.944939
  - mean_roc_auc: 0.618640
- honest limitations:
  - sparse event-window folds
  - chronological holdout may have no positives
  - not production accuracy
  - P5MIN/Predispatch, outages, regional demand, and interconnector inputs are future extensions

Keep README concise.
Do not let it become a long execution log.

4. Notebook final pass

Update:
notebooks/aemo_constraint_forecast_demo.ipynb

The notebook should tell the final story:
- problem statement
- public AEMO data sources
- DUID -> connection point -> GENCONID mapping
- target discovery
- why CATHROCK was selected
- future-only 6h horizon label
- Phase 3 benchmark result
- best model
- API query
- limitations

Clear notebook outputs unless small, useful tables are intentionally included.

5. Add application materials

Create:

application/project_summary.md
application/resume_bullets.md
application/interview_talking_points.md

project_summary.md should include:
- 1 paragraph project overview
- technical stack
- data sources
- model approach
- evaluation approach
- limitations
- future improvements

resume_bullets.md should include 4 to 6 concise resume bullets, for example:
- Built a FastAPI demo service forecasting AEMO NEM constraint risk for a selected DUID using public NEMWeb dispatch and constraint data.
- Engineered future-only 6h horizon labels and leakage-guarded time/weather/dispatch-lag features for sparse-event classification.
- Implemented DUID target discovery across public AEMO data to identify CATHROCK as a viable constrained-generator demo target.
- Benchmarked logistic regression, random forest, and gradient boosting models using event-window backtests, improving mean AP from 0.429 to 0.615.
- Packaged model artifacts, API examples, tests, and documentation into a GitHub-ready internship application project.

interview_talking_points.md should include:
- why the first OAKEY1SF attempt failed
- why target discovery was needed
- why horizon labels are better than 5-minute labels
- why dispatch-lag features helped
- why results should not be overstated
- what production extensions would be next

6. Final tests and smoke test

Run:

python -m pytest -q

Then run API smoke test with inferred horizon:

python - <<'PY'
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
response = client.get(
    "/forecast/CATHROCK",
    params={
        "model_path": "artifacts/cathrock_6h_best_model.joblib",
        "start": "2026-05-20 00:00:00",
    },
)
print(response.status_code)
payload = response.json()
print(payload["duid"], payload["horizon_hours"], payload["model"]["model_type"], len(payload["forecast"]))
PY

Expected:
- status_code == 200
- duid == CATHROCK
- horizon_hours == 6
- model_type == logistic_regression
- forecast length > 0

Also test explicit mismatch:

python - <<'PY'
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
response = client.get(
    "/forecast/CATHROCK",
    params={
        "horizon_hours": 24,
        "model_path": "artifacts/cathrock_6h_best_model.joblib",
    },
)
print(response.status_code)
print(response.text[:300])
PY

Expected:
- status_code == 400

7. Git hygiene

Run:

git status
git diff --stat

Check:
- no large raw AEMO files
- no data/cache files
- no .venv
- no __pycache__
- no .pytest_cache
- no unrelated deleted files unless intentional

8. Final report

Create:

chatref/phase4_final_polish_report.md

Report must include:
- timestamp
- files changed
- tests result
- API inferred-horizon smoke test result
- API mismatch-horizon test result
- README updated yes/no
- notebook updated yes/no
- application materials created yes/no
- final artifacts
- remaining limitations
- whether ready to push to GitHub
- exact git commands recommended

Final response format:
- Summary
- Files changed
- Tests
- API smoke result
- README/notebook status
- Application materials
- Git status summary
- Ready for GitHub: yes/no
- Next exact commands
```

---

# 建议新增 `phase4.md`

可以让 Codex 创建：

```text
plan/phase4_final_polish.md
```

内容不需要很长，核心就是：

```md
# Phase 4 — Final GitHub Polish and Application Packaging

## Objective

Prepare the AEMO DUID network constraint forecasting demo for GitHub submission and Proa Analytics internship application review.

## Scope

- Verify README commands work.
- Ensure API uses the Phase 3 best model.
- Ensure horizon inference works.
- Regenerate example response.
- Polish README and notebook.
- Add application materials.
- Run final tests.
- Check git hygiene.

## Definition of Done

- `python -m pytest -q` passes.
- API works with inferred `horizon_hours`.
- API rejects mismatched explicit horizon.
- README top section clearly explains the demo and result.
- Notebook tells the final story.
- Application materials exist.
- No large raw AEMO/cache files are committed.
- Final report exists.
```

---

# Phase 4 完成后你给我什么

这次我建议你上传 **整个最终 repo zip**，因为我要看的是一致性，不只是单个文件。

在项目上一级目录执行：

```bash
cd /Users/liuzhenqian/Desktop

zip -r aemo-network-constraint-demo-final.zip aemo-network-constraint-demo \
  -x "*/.venv/*" \
  -x "*/__pycache__/*" \
  -x "*/.pytest_cache/*" \
  -x "*/data/cache/*" \
  -x "*/.git/*"
```

然后上传：

```text
aemo-network-constraint-demo-final.zip
```

如果不传整个 repo，至少传这些：

```text
chatref/phase4_final_polish_report.md
README.md
app.py
src/aemo_constraint_demo/forecast.py
src/aemo_constraint_demo/train.py
src/aemo_constraint_demo/features.py
scripts/benchmark_models.py
artifacts/performance_benchmark.csv
artifacts/cathrock_6h_best_model_metrics.json
artifacts/cathrock_6h_feature_importance.csv
artifacts/example_cathrock_6h_response.json
application/project_summary.md
application/resume_bullets.md
application/interview_talking_points.md
notebooks/aemo_constraint_forecast_demo.ipynb
```

---

# 之后就是最后一步

Phase 4 通过后，就可以：

```bash
git add .
git commit -m "Build AEMO DUID constraint forecasting demo"
gh repo create aemo-network-constraint-demo --public --source=. --remote=origin --push
```

然后准备投递材料。

项目到现在已经不适合继续无限加功能了。对实习申请来说，最有价值的是把故事讲清楚：

```text
我从 AEMO public data 出发，发现原始目标正样本太稀疏；
通过 target discovery 找到 CATHROCK；
用 future-only 6h horizon label 转成可预测问题；
用 time/weather/dispatch-lag features 做 sparse-event classification；
用 event-window backtest 诚实评估；
最后封装成 FastAPI 查询服务。
```
