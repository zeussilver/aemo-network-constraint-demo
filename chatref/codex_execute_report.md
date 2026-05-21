# Codex Execute Report - phase0

生成时间：2026-05-19 20:46:01 AEST  
项目目录：`/Users/liuzhenqian/Desktop/aemo-network-constraint-demo`

## 本次目标

修复终端无法运行训练脚本的问题，并完成本阶段重要变更与结果归档。

原始训练命令：

```bash
python -m src.aemo_constraint_demo.train \
  --duid OAKEY1SF \
  --lat -27.43 \
  --lon 151.72 \
  --days 14 \
  --model-out artifacts/oakey1sf.joblib
```

## 发现的问题与修复

1. `latest_mms_month()` 无法解析 NEMWeb 最新 MMSDM 年份目录。
   - 原因：当前 NEMWeb 目录页使用大写 `HREF`，原正则是大小写敏感。
   - 修复：年份目录解析改为大小写不敏感。

2. post-2024 MMSDM 单表 ZIP 下载失败。
   - 原因：NEMWeb 当前对文件名中的 `#` 要求双重编码，单次 `%23` 请求返回 400。
   - 修复：`monthly_archive_url()` 改为生成 `%2523` 编码 URL。

3. Next Day Dispatch 解析后 `DISPATCHLOAD` 为空。
   - 原因：当前 daily ZIP 中单位调度表实际为 `DISPATCH,UNIT_SOLUTION`，不是直接的 `DISPATCHLOAD`。
   - 修复：在 parser 中加入表别名，继续对训练脚本暴露统一键 `DISPATCHLOAD`。

4. 训练日志在终端中长时间无输出。
   - 原因：非交互运行时 `print` 缓冲。
   - 修复：关键训练进度输出使用 `flush=True`。

5. `OAKEY1SF` 最近 14 天训练窗口无正样本，`LogisticRegression` 不能训练单一类别。
   - 原因：`target_constrained` 全部为 0。
   - 修复：新增 `ConstantProbabilityClassifier` 基线模型；当训练窗口只有一个类别时自动 fallback，并在 metrics 中记录 `model_type=constant_probability_baseline`。
   - 额外处理：该模型类放入 `src/aemo_constraint_demo/models.py`，避免通过 `python -m ...train` 训练后 FastAPI 另进程加载 joblib 时找不到 `__main__` 类。

## 修改文件

- `src/aemo_constraint_demo/aemo_client.py`
- `src/aemo_constraint_demo/train.py`
- `src/aemo_constraint_demo/models.py`
- `src/aemo_constraint_demo/__init__.py`
- `tests/test_aemo_client.py`
- `tests/test_train.py`

## 验证结果

单元测试：

```bash
python -m pytest -q
```

结果：

```text
28 passed in 1.15s
```

训练命令已成功完成，并生成：

```text
artifacts/oakey1sf.joblib
```

模型文件大小：

```text
12K
```

## 本次训练结果摘要

- DUID：`OAKEY1SF`
- connection point：`QTKS1O`
- relevant GENCONID 数量：`15`
- feature 数量：`14`
- 训练样本数：`3225`
- 测试样本数：`807`
- 总样本数：`4032`
- 正样本比例：`0.0`
- 训练正样本比例：`0.0`
- 测试正样本比例：`0.0`
- model type：`constant_probability_baseline`
- test average precision：`null`
- test ROC AUC：`null`

重要解释：本次 14 天窗口内没有观察到 `OAKEY1SF` 相关约束绑定样本，因此生成的是可运行、可查询的常数概率基线模型，不是有判别能力的 logistic regression 模型。若要训练真正的分类模型，需要扩大历史窗口、换 DUID，或接入更长历史 dispatch/constraint 数据。

## 后续约定

以后需要展示变更报告和重要执行结果时，同步复制一份到对应阶段的 `chatref` 子目录。本次阶段名为 `phase0`，报告文件为：

```text
chatref/codex_execute_report.md
```
