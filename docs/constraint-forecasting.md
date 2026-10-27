# Constraint forecasting

The question:

> For a given AEMO Dispatchable Unit ID (`DUID`), forecast the next 1–72 hours in which
> network-related generic constraints are likely to bind.

It uses AEMO public NEMWeb data for dispatch labels and constraint mapping, adds weather and
time features, and trains a small classifier. The result is served three ways: a FastAPI
endpoint, an MCP server, and the risk card in the site ops console. The shipped model covers
one unit, Cathedral Rocks (`CATHROCK`). The [README](../README.md#constraint-forecasting) has
the summary; this page has the method and every run behind it.

## Result

- Target: `CATHROCK`, 6-hour horizon.
- Best model: `logistic_regression` with `time_weather_dispatch_lag` features.
- Event-window backtest, mean of 2 folds: AP `0.615`, ROC AUC `0.619`, precision at top 5%
  `0.633`, lift at top 5% `1.94`.

Lead-time buckets ([Phase 6](#phase-6-lead-time-buckets)) split that horizon into per-hour
windows. Every bucket scores far below the whole-horizon model trained in the same run: mean
AP `0.09`–`0.17` against `0.605`, with lift at top 5% below `1.0`. The model can say a constraint is coming, but not in
which hour.

The evidence is thin: the 60-day window contains 3 binding events, and the chronological
holdout has no positive 6-hour labels. See [Known limitations](#known-limitations).

## How it works

`GET /forecast/{duid}` returns the ML forecast plus the planned and invoked constraints for the
unit. Five parts:

1. **AEMO public data ingestion**
   - Downloads AEMO standing-data tables from MMSDM monthly archives.
   - Downloads recent `Next Day Dispatch` public files instead of full-month dispatch tables.
2. **DUID → constraint mapping**
   - Maps `DUID -> CONNECTIONPOINTID` using `DUDETAIL`.
   - Maps `CONNECTIONPOINTID -> GENCONID` using `SPDCONNECTIONPOINTCONSTRAINT`.
3. **Historical labels**
   - Uses `DISPATCHCONSTRAINT` rows for the relevant `GENCONID`s.
   - The target is `1` when any relevant constraint is binding or violating at a dispatch
     interval; training uses a future-only horizon label built from it.
4. **Model**
   - Features: time of day, day of week, month, weekend flag, weather, and current-or-past
     dispatch lags.
   - Logistic regression, for speed and explainability.
5. **Query service**
   - `GET /forecast/{duid}` returns ML probabilities and a planned/invoked-constraint view.
   - The same pipeline is exposed as four MCP tools.

## Data sources

AEMO/NEMWeb:

- MMSDM monthly archive root:
  `https://www.nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/`
- Current Next Day Dispatch files: `https://www.nemweb.com.au/Reports/CURRENT/Next_Day_Dispatch/`.
  They hold unit dispatch and constraint solutions for each 5-minute dispatch run of the
  previous trading day.
- Tables used:
  - `DISPATCHCONSTRAINT`: binding/interregion constraints, RHS, LHS, marginal value, violation
    degree.
  - `DUDETAIL`: dispatchable unit detail, including connection-point mapping.
  - `SPDCONNECTIONPOINTCONSTRAINT`: connection-point LHS factors for generic constraints.
  - `GENCONSET`: generic constraint set membership.
  - `GENCONSETINVOKE`/`GENCONDATA`: constraint-set invocation and constraint metadata.

Weather: the Open-Meteo [historical](https://open-meteo.com/en/docs/historical-weather-api) and
[forecast](https://open-meteo.com/en/docs) APIs. A production version would use whichever
weather/NWP feed the site operator prefers.

## Label definition

For a selected `DUID`, first find the generic constraints that include the DUID's connection
point on the LHS. For dispatch interval `t`:

```text
target_constrained(t, DUID) = 1
if any relevant GENCONID has MARGINALVALUE > 0 or VIOLATIONDEGREE > 0 in DISPATCHCONSTRAINT
else 0
```

For a forecast horizon `H`:

```text
target_constrained_next_Hh(t, DUID) = 1
if target_constrained is 1 in any future dispatch interval from t+1 through t+H
else 0
```

The current interval is excluded from the horizon label. Training drops the final incomplete
horizon window instead of turning unknown future intervals into false negatives.

The definition is simple so it can be checked by hand. A production version would refine it
with AEMO's constraint taxonomy, outage context, P5MIN/Predispatch constraint solutions, and
rules separating network from system constraints.

## Runs

Each run below was made in order; its commands reproduce it. Set up the Python environment as
in the [README](../README.md#run-it) first.

### Training smoke test

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib
```

`CATHROCK` coordinates are read from `data/duid_coordinates.csv`; override them with `--lat`
and `--lon`. For a faster run, reduce `--days`, or add `--no-weather` to skip Open-Meteo.

The script prints a JSON summary like:

```json
{
  "model_out": "artifacts/cathrock_6h.joblib",
  "metrics_out": "artifacts/cathrock_6h_metrics.json",
  "duid": "CATHROCK",
  "connection_point": "SCRK",
  "horizon_hours": 6,
  "target_column": "target_constrained_next_6h",
  "relevant_genconids_count": 103,
  "feature_columns": ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "temperature_2m"],
  "metrics": {
    "model_type": "balanced_logistic_regression",
    "total_samples": 17208,
    "positive_rate": 0.013249651324965132,
    "average_precision": null,
    "roc_auc": null,
    "supplemental_positive_backtest": {
      "available": true,
      "average_precision": 0.4725645845784202,
      "roc_auc": 0.3285603715170279
    }
  }
}
```

The metrics depend on the DUID, the date window and the current NEMWeb rolling files. If the
training split has one target class, the script saves a `constant_probability_baseline` model
instead of failing. If the held-out set has one class, `average_precision` and `roc_auc` are
recorded as `null`.

### Phase 1: target discovery

Scan candidate DUIDs and time windows for targets with real positive constraint labels:

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

The scan writes `artifacts/duid_constraint_scan.csv` and a ranked
`artifacts/duid_constraint_scan_top.csv`. Use `positive_intervals` and `positive_rate` to pick
the training target: zero-positive DUIDs fall back to the constant baseline, while recommended
rows have enough sparse positives for a classifier.

### Phase 1.5: forecast-horizon labels

The direct 5-minute binding label is too sparse to forecast. Phase 1.5 keeps it and adds
future-only horizon labels:

```text
target_constrained_next_1h
target_constrained_next_3h
target_constrained_next_6h
```

Each is `1` when a relevant constraint binds in a future dispatch interval inside that window.
The current interval is excluded, so a row's features can stay current-or-past only.

```bash
python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --horizons-hours 1 3 6 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --top-out artifacts/duid_constraint_scan_expanded_top.csv \
  --verbose
```

The scan writes one row per `DUID`/window/horizon. Pick the target from the ranked top CSV:
prefer rows where `recommended` is true, then higher `positive_intervals`, a practical
`positive_rate`, and a recent `last_positive_dispatch_interval`.

### Phase 2: horizon-based training

Target: `CATHROCK` (connection point `SCRK`), 60 days, 6-hour horizon,
`target_constrained_next_6h`.

```text
model_type: balanced_logistic_regression
total_samples: 17208
train_samples: 13766
test_samples: 3442
positive_rate: 0.013
train_positive_rate: 0.017
test_positive_rate: 0.0
average_precision: null
roc_auc: null
precision_at_top_5_percent: 0.0
```

The chronological test window has no positive horizon events, so AP and ROC AUC are `null`.

The trainer also records a supplemental backtest on the latest 24-hour window with positive
labels, training only on rows before it:

```text
validation_start: 2026-04-22T22:20:00
validation_end: 2026-04-23T22:15:00
train_samples: 9723
train_positive_samples: 76
validation_samples: 288
validation_positive_samples: 152
validation_positive_rate: 0.528
average_precision: 0.473
roc_auc: 0.329
precision_at_top_5_percent: 0.8
```

Time and weather features alone rank this window poorly (ROC AUC `0.33`), though the top 5% of
probabilities still catch some high-risk intervals.

Artifacts: `artifacts/cathrock_6h_metrics.json`, `artifacts/example_cathrock_6h_response.json`.

### Phase 2.5: event-window backtest

With no positives in the chronological holdout, ranking around rare events is tested with
event-window folds, each trained only on rows strictly before its validation window:

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

```text
evaluated_folds: 2
skipped_folds: 1
mean_average_precision: 0.429
mean_roc_auc: 0.535
mean_precision_at_top_5_percent: 0.433
mean_lift_at_top_5_percent: 0.886
```

The pipeline can be evaluated without future leakage, and some ranking signal exists, but lift
below `1.0` means the top 5% does not reliably concentrate the positive intervals.

Artifacts: `artifacts/cathrock_6h_backtest_summary.json`,
`artifacts/cathrock_6h_backtest_folds.csv`, `artifacts/example_cathrock_6h_backtest_response.json`.

### Phase 3: feature sets and model benchmark

Leakage-guarded feature sets, a model benchmark, and a best-model bundle for the same target:

```bash
python scripts/benchmark_models.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --feature-sets time_only time_weather time_weather_dispatch_lag time_weather_constraint_lag \
  --model-types logistic_regression random_forest hist_gradient_boosting \
  --out artifacts/performance_benchmark.csv \
  --best-model-out artifacts/cathrock_6h_best_model.joblib \
  --report-out docs/reports/phase3_performance_report.md
```

Best setting:

```text
model: logistic_regression
feature_set: time_weather_dispatch_lag
evaluated_folds: 2
skipped_folds: 1
mean_average_precision: 0.615
mean_roc_auc: 0.619
mean_precision_at_top_5_percent: 0.633
mean_lift_at_top_5_percent: 1.945
```

Against Phase 2.5's event-window AP of `0.429`, the dispatch-lag features improved ranking on
the same sparse folds. This bundle, `artifacts/cathrock_6h_best_model.joblib`, is the default
for the API examples and the MCP server. The console's risk card is scored by the hourly ingest
with the [Phase 6](#phase-6-lead-time-buckets) bundle, `artifacts/cathrock_lead_time_models.joblib`.

Artifacts: `artifacts/performance_benchmark.csv`, `artifacts/cathrock_6h_best_model.joblib`,
`artifacts/cathrock_6h_best_model_metrics.json`, `artifacts/cathrock_6h_feature_importance.csv`,
[`docs/reports/phase3_performance_report.md`](reports/phase3_performance_report.md).

### Phase 5: forecast-time feature parity

Phase 5 fixes a train/serve skew, not the model.

Training builds one row per dispatch interval `t` (time features at `t`, weather at `t`,
dispatch lags from strictly before `t`) and labels it "does a relevant constraint bind in
`(t, t+6h]`". The Phase 3 metrics were measured against that frame.

Serving did something else. It generated 72 future 5-minute intervals and scored each as its
own row. Dispatch-lag features cannot exist for a future interval, so 36 of the bundle's 50
features were filled with training medians, and the output was labelled as a per-interval
probability, which is not what any of those rows meant.

Replaying the committed bundle over cached NEMWeb data around a real binding event:

```text
origin 2026-04-22 06:00, constraint actually bound at 06:20

old serving path (36/50 features imputed):   p = 0.0003
same bundle, features as training built them: p = 0.807
```

The fix has three parts:

1. `src/aemo_constraint_demo/frames.py`: one `build_model_frame` shared by training,
   backtesting and serving. Each previously carried its own copy of the pipeline, which is
   how they drifted apart.
2. `forecast_from_bundle` is origin-anchored: it loads recent dispatch, scores the **latest
   observed** interval, and returns the one probability the model estimates, plus `as_of`,
   `data_lag_hours`, the invoked-constraint timeline and a 24-hour risk trajectory.
3. `tests/test_frames.py` asserts that the training and serving frames produce identical
   feature values for the same origin, so the skew cannot come back unnoticed.

The replay above is partly in-sample (the bundle trained on a window containing April), so it
shows the old serving path was discarding the model, not that the model is accurate. The
out-of-sample numbers are still Phase 3's, because `backtest_horizon_model.py` always used the
correct frame. Probabilities are close to saturated (typically `<1e-15` or `>0.9`), so read
them as a ranking rather than a calibrated risk. The origin trails real time by 4–28 hours
because Next Day Dispatch publishes the previous trading day.

### Phase 6: lead-time buckets

One probability for the whole horizon cannot say when inside the window the risk sits. Phase 6
adds disjoint lead-time buckets that tile the horizon:

```text
target_constrained_lead_Xh(t) = 1 if a relevant constraint binds in (t+(X-1)h, t+Xh]
```

One model per bucket, all scored from the same origin with the same origin-anchored features,
so one call produces a forward curve without inventing feature values for future intervals.
The buckets reuse the Phase 3 winner rather than re-running the feature-set benchmark.

```bash
python scripts/train_lead_time_models.py \
  --duid CATHROCK --days 60 --horizon-hours 6 --lead-hours 1 2 3 4 5 6 \
  --source cache --end-date 2026-05-18 \
  --feature-set time_weather_dispatch_lag --model-type logistic_regression \
  --model-out artifacts/cathrock_lead_time_models.joblib \
  --metrics-out artifacts/cathrock_lead_time_metrics.json \
  --folds-out artifacts/cathrock_lead_time_folds.csv \
  --report-out docs/reports/phase6_lead_time_report.md
```

Query it like any other bundle; the response gains `risk_curve` and `peak_lead`:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_lead_time_models.joblib&source=cache"
```

| Target | Positives | Folds | Mean AP | Mean ROC AUC | Mean lift@top-5% |
| --- | --- | --- | --- | --- | --- |
| Cumulative `next_6h` | 228 | 2 | `0.605` | `0.609` | `1.818` |
| Bucket `lead_1h` | 48 | 2 | `0.128` | `0.570` | `0.567` |
| Bucket `lead_2h` | 48 | 2 | `0.166` | `0.535` | `0.567` |
| Bucket `lead_3h` | 48 | 2 | `0.094` | `0.375` | `0.567` |
| Bucket `lead_4h` | 48 | 2 | `0.152` | `0.625` | `0.567` |
| Bucket `lead_5h` | 48 | 2 | `0.137` | `0.655` | `0.567` |
| Bucket `lead_6h` | 48 | 2 | `0.151` | `0.678` | `0.567` |

Every bucket scores far below the cumulative model, and lift under `1.0` means the
highest-scoring intervals hold fewer events than a random slice would. A replay anchored at
`2026-04-23 04:00`, ahead of a constraint that bound at 06:20, shows the same thing:

```text
lead 1h  04:00–05:00  p=0.8645  high
lead 2h  05:00–06:00  p=0.9801  high
lead 3h  06:00–07:00  p=0.9918  high   <- the window the event actually fell in
lead 4h  07:00–08:00  p=0.9948  high   <- the curve's peak
lead 5h  08:00–09:00  p=0.9836  high
lead 6h  09:00–10:00  p=0.2804  medium
```

The curve is high across almost the whole horizon and peaks one bucket late: dispatch-lag
features carry "conditions are constraint-prone", not "at hour k". Two causes are visible in
the data. The 60-day window contains only **3 binding events** (14 dispatch intervals, all
near 06:00 or 22:00), and bucket labels are 5x sparser than the cumulative label by
construction. Only 2 of 3 candidate folds can be evaluated, because nothing before the
earliest event gives a two-class training split.

The identical `0.567` lift across buckets is small-sample quantisation, not a bug: the top 5%
of a 289-row window is about 15 rows, and every bucket gets exactly one true positive there.
The per-bucket AP, ROC AUC and fitted coefficients do differ.

So read the forward curve as a shape, not a calibrated timing forecast. Timing needs
P5MIN/Predispatch constraint solutions, outage schedules, and many more than three events.

Artifacts: `artifacts/cathrock_lead_time_models.joblib`,
`artifacts/cathrock_lead_time_metrics.json`, `artifacts/cathrock_lead_time_folds.csv`,
[`docs/reports/phase6_lead_time_report.md`](reports/phase6_lead_time_report.md).

## Query service

```bash
uvicorn app:app --reload
```

Forecast from NEMWeb:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&source=network"
```

From previously downloaded files only, without weather:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&source=cache&use_weather=false"
```

Replay a historical origin, which is how the model is checked against a known binding event:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&source=cache&end_date=2026-04-22"
```

The API infers `horizon_hours=6` from the model bundle when the query parameter is omitted; an
explicit horizon that disagrees with the bundle returns a `400`.

The answer is origin-anchored: one probability for the horizon starting at the latest dispatch
interval NEMWeb has published. `as_of` and `data_lag_hours` say how stale that origin is. Next
Day Dispatch publishes the previous trading day, so nothing here is live. `feature_coverage`
reports how much of the model's input was observed rather than imputed; a non-zero `imputed`
count means the probability rests on incomplete inputs.

Example response (trimmed; the full one is `artifacts/example_cathrock_6h_response.json`):

```json
{
  "duid": "CATHROCK",
  "connection_point": "SCRK",
  "horizon_hours": 6,
  "as_of": "2026-08-29T04:00:00",
  "horizon_end": "2026-08-29T10:00:00",
  "data_lag_hours": 9.19,
  "constraint_risk": {
    "probability": 0.0,
    "risk_level": "very_low",
    "statement": "P(a relevant generic constraint binds for CATHROCK between 2026-08-29T04:00:00 and 2026-08-29T10:00:00)"
  },
  "feature_coverage": {"required": 50, "observed": 50, "imputed": 0, "imputed_examples": []},
  "dispatch_source": {
    "source": "network",
    "history_days": 2,
    "first_interval": "2026-08-27T04:05:00",
    "last_interval": "2026-08-29T04:00:00",
    "interval_count": 576
  },
  "weather_source": "open_meteo_history",
  "model": {
    "model_type": "logistic_regression",
    "feature_set": "time_weather_dispatch_lag",
    "target_column": "target_constrained_next_6h",
    "positive_rate": 0.013249651324965132
  },
  "relevant_genconids_count": 103,
  "planned_constraints": [],
  "recent_risk_history": [
    {"origin": "2026-08-29T03:00:00", "probability": 0.0, "risk_level": "very_low"}
  ]
}
```

## MCP server

The [README](../README.md#mcp-server) has the install command and the tool list.

For an MCP client other than Claude Code, the stdio config is:

```json
{
  "mcpServers": {
    "aemo-nemweb": {
      "command": "/abs/path/to/aemo-network-constraint-demo/.venv/bin/python",
      "args": ["/abs/path/to/aemo-network-constraint-demo/run_mcp_server.py"]
    }
  }
}
```

`run_mcp_server.py` resolves data, artifact and model paths against the repository root, so
the client can launch it from any working directory.

`aemo_list_duid_targets` reads the committed scan artifacts and never touches the network. The
other three tools take a `source` argument:

- `cache`: previously downloaded NEMWeb files only;
- `network`: always fetch from NEMWeb (roughly 3–5 seconds per trading day);
- `auto` (default): use the cache when it already covers the requested window.

Parsed trading days are cached in-process. `data/cache/` is gitignored, so a fresh clone starts
with an empty cache and `auto` will download.

Example prompts:

```text
Which DUIDs in this demo have enough binding-constraint history to train on?
Show CATHROCK's dispatch for the last 2 trading days.
Which of CATHROCK's constraints bound on 22-23 April 2026?
What is CATHROCK's constraint risk for the next 6 hours, and how stale is the input data?
Replay CATHROCK's 22 April 2026 forecast and compare it against what actually bound.
```

Design notes:

- The forecast tool returns one probability for the next `horizon_hours`, which is what the
  model estimates, rather than a per-interval curve it was never trained to produce
  ([Phase 5](#phase-5-forecast-time-feature-parity)).
- Answers carry `as_of` and `data_lag_hours`, and the server instructions tell the agent to
  quote them.
- `feature_coverage` reports how many of the model's features were observed at the origin
  versus imputed; the tool says so when inputs are incomplete.
- An unknown DUID, a missing bundle, or a horizon that disagrees with the bundle returns a
  message naming the fix and the tool to call next, not a stack trace.
- Long series are downsampled evenly (keeping the first and last interval). The 24-hour risk
  trajectory comes back hourly, and the invoked-constraint timeline is aggregated per
  constraint rather than per five-minute row.

```bash
python run_mcp_server.py            # stdio, for a client to attach to
python -m pytest tests/test_mcp_server.py
```

## Notebook

```bash
jupyter notebook notebooks/aemo_constraint_forecast_demo.ipynb
```

It walks through target discovery and why `CATHROCK` was chosen, the
`DUID -> CONNECTIONPOINTID -> GENCONID` mapping, the `target_constrained_next_6h` label, the
training command and metrics, the event-window backtest, API query examples, and limitations.

## Design choices

**Next Day Dispatch files.** Full MMSDM monthly dispatch tables can be hundreds of MB per
table. Next Day Dispatch files are small daily ZIPs and still include 5-minute unit dispatch
and constraint solutions for the previous trading day.

**Logistic regression.** Balanced logistic regression is fast, stable on small windows, easy to
explain, and easy to compare against a baseline. Gradient boosting, sequence models or
probabilistic forecasting could replace it once there are enough events to train them.

**What "planned" means.** The service's planned/invoked view comes from `GENCONSET`,
`GENCONSETINVOKE` and `GENCONDATA`. It identifies relevant constraint sets active over future
intervals where available. It is not a P5MIN/Predispatch forecast-solution integration; that
would add:

- `P5MIN_CONSTRAINTSOLUTION` for near-term 5-minute forecast solutions;
- `PREDISPATCHCONSTRAINT` or its successor for longer-horizon forecast solutions;
- constraint-set outage descriptions and limit-type metadata.

## Known limitations

1. **Sparse events.** The 60-day window has 3 binding events. The chronological holdout has no
   positive `CATHROCK` 6-hour labels, and the event-window folds are a supplement, not proof
   of accuracy.
2. **Network vs system constraints are not separated.** Every DUID-relevant generic constraint
   counts as the target class.
3. **One model per DUID.** Multi-DUID modelling would need a shared feature store and DUID
   features.
4. **Short training window.** Recent rolling files only; no long history or seasonality.
5. **Minimal forecast-time features.** Time and weather by default. Regional demand,
   interconnector forecasts, UIGF, outage schedules and P5MIN/Predispatch inputs are not used.
6. **Tiny geospatial lookup.** `data/duid_coordinates.csv` holds one vetted row, for
   `CATHROCK`.

## Next production extensions

- P5MIN and Predispatch forecast-constraint solution ingestion.
- Constraint taxonomy: thermal, voltage stability, transient stability, system strength, FCAS.
- Long-run historical backtesting and grouped time-series cross-validation.
- DUID geospatial metadata and weather-grid matching.
- Calibrated probabilities and thresholds tuned to a business objective.
- Lead-time buckets again once there are far more than 3 binding events, with
  P5MIN/Predispatch constraint solutions to carry the timing signal.
- Monitoring for data freshness, AEMO schema changes and model drift.
