# AEMO DUID Network Constraint Forecast Demo

A compact, GitHub-ready demo for a Proa Analytics internship application.

The demo answers a narrow but realistic question:

> For a given AEMO Dispatchable Unit ID (`DUID`), forecast the next 1–72 hours in which network-related generic constraints are likely to bind.

It uses AEMO public NEMWeb data for dispatch labels and constraint mapping, adds weather/time features, trains a small ML classifier, and exposes a queryable FastAPI endpoint.

---

## Demo result

- Selected target: `CATHROCK`
- Selected horizon: `6h`
- Best model: `logistic_regression` with `time_weather_dispatch_lag` features
- Mean event-window AP: `0.615064`
- Mean precision@top 5%: `0.633333`
- Mean lift@top 5%: `1.944939`
- Mean ROC AUC: `0.618640`

This is a workflow demo, not a production forecast model. The event-window folds are sparse, the chronological holdout has no positive `CATHROCK` 6h labels, and P5MIN/Predispatch, outage, regional demand, and interconnector inputs are future extensions.

---

## Why this maps to the internship brief

The internship brief asks for a service that can be queried for a `DUID` and return planned AEMO constraints plus ML-forecast constraints.

This demo implements a minimum viable version:

1. **AEMO public data ingestion**
   - Downloads AEMO standing-data tables from MMSDM monthly archives.
   - Downloads recent `Next Day Dispatch` public files instead of huge full-month dispatch tables.

2. **DUID → constraint mapping**
   - Maps `DUID -> CONNECTIONPOINTID` using `DUDETAIL`.
   - Maps `CONNECTIONPOINTID -> GENCONID` using `SPDCONNECTIONPOINTCONSTRAINT`.

3. **Historical label construction**
   - Uses `DISPATCHCONSTRAINT` rows for relevant `GENCONID`s.
   - Defines the target as `1` when any relevant constraint is binding or violating at a dispatch interval.
   - Phase 2 trains against a future-only horizon label for the selected DUID.

4. **ML forecast model**
   - Features: time-of-day, day-of-week, month, weekend flag, weather, and current-or-past dispatch lag signals.
   - Selected Phase 3 model: logistic regression for speed and explainability.

5. **Queryable service**
   - `GET /forecast/{duid}` returns ML probabilities and a lightweight planned/invoked-constraint view.

---

## Data sources

### AEMO/NEMWeb

- **MMSDM monthly archive root**: `https://www.nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/`
- **Current Next Day Dispatch rolling files**: `https://www.nemweb.com.au/Reports/CURRENT/Next_Day_Dispatch/`
- **AEMO Dispatch data description**: `Next Day Dispatch` contains unit dispatch and constraint solutions for each 5-minute dispatch run of the previous trading day.
- **AEMO table docs used**:
  - `DISPATCHCONSTRAINT`: binding/interregion constraints, RHS, LHS, marginal value, violation degree.
  - `DUDETAIL`: dispatchable unit detail, including connection-point mapping.
  - `SPDCONNECTIONPOINTCONSTRAINT`: connection-point LHS factors for generic constraints.
  - `GENCONSET`: generic constraint set membership.
  - `GENCONSETINVOKE`/`GENCONDATA`: constraint-set invocation and constraint metadata.

### Weather

- **Open-Meteo historical weather API**: `https://open-meteo.com/en/docs/historical-weather-api`
- **Open-Meteo forecast API**: `https://open-meteo.com/en/docs`

Open-Meteo is used only for a demo weather signal. A production version should use whichever weather/NWP feed Proa prefers.

---

## Label definition

For a selected `DUID`, first identify the generic constraints that include the DUID's connection point on the LHS.

For dispatch interval `t`:

```text
target_constrained(t, DUID) = 1
if any relevant GENCONID has MARGINALVALUE > 0 or VIOLATIONDEGREE > 0 in DISPATCHCONSTRAINT
else 0
```

For a forecast horizon `H`, Phase 2 uses:

```text
target_constrained_next_Hh(t, DUID) = 1
if target_constrained is 1 in any future dispatch interval from t+1 through t+H
else 0
```

The current interval is excluded from the horizon label. Training drops the final incomplete horizon window instead of
turning unknown future intervals into false negatives.

This is intentionally simple and auditable. The real production version should refine the definition using AEMO constraint taxonomy, outage context, P5MIN/Predispatch constraint solution data, and domain-specific rules for network-vs-system constraints.

---

## Repository structure

```text
.
├── README.md
├── requirements.txt
├── app.py
├── application/
│   ├── interview_talking_points.md
│   ├── project_summary.md
│   └── resume_bullets.md
├── notebooks/
│   └── aemo_constraint_forecast_demo.ipynb
├── scripts/
│   ├── benchmark_models.py
│   ├── backtest_horizon_model.py
│   └── find_constrained_duids.py
├── tests/
│   ├── test_aemo_client.py
│   ├── test_api.py
│   ├── test_backtest.py
│   ├── test_benchmark_models.py
│   ├── test_features.py
│   ├── test_forecast.py
│   ├── test_notebook_readme.py
│   ├── test_train.py
│   └── test_weather.py
└── src/aemo_constraint_demo/
    ├── aemo_client.py       # NEMWeb download + AEMO CSV parsing
    ├── features.py          # DUID mapping, labels, time features
    ├── weather.py           # Open-Meteo history/forecast helpers
    ├── train.py             # CLI model training
    └── forecast.py          # model-bundle query logic
```

---

## Quick start

### 1. Create environment

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Use or regenerate the selected model

The final API example uses the small Phase 3 best-model bundle:

```text
artifacts/cathrock_6h_best_model.joblib
```

If that bundle is missing, regenerate it with the Phase 3 benchmark command in the performance section below. For a simpler training smoke test, run:

```bash
python -m src.aemo_constraint_demo.train \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --model-out artifacts/cathrock_6h.joblib
```

`CATHROCK` coordinates are read from `data/duid_coordinates.csv`. You can override them with `--lat` and `--lon`.
For a faster smoke test, reduce `--days`, or add `--no-weather` to skip Open-Meteo.

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

The exact metrics depend on the chosen DUID, date window, and current NEMWeb rolling files.
If the training split has one target class, the script saves a `constant_probability_baseline` model instead of failing.
If the held-out set has one target class, `average_precision` and `roc_auc` are recorded as `null`.

### Phase 1: Target Discovery

Before training a meaningful classifier, scan candidate DUIDs and time windows to find targets with real positive
constraint labels:

```bash
python scripts/find_constrained_duids.py \
  --days 30 60 \
  --max-duids 50 \
  --out artifacts/duid_constraint_scan.csv \
  --verbose
```

The scan writes `artifacts/duid_constraint_scan.csv` and a ranked
`artifacts/duid_constraint_scan_top.csv`. Use `positive_intervals` and `positive_rate` to pick the next training
target: zero-positive DUIDs are useful smoke tests but will fall back to the constant baseline, while recommended rows
have enough sparse positives for a baseline ML classifier.

### Phase 1.5: Forecast-Horizon Target Discovery

The direct 5-minute binding label can be too sparse for a useful forecasting demo. Phase 1.5 keeps the auditable
current-interval label and adds future-only horizon labels:

```text
target_constrained_next_1h
target_constrained_next_3h
target_constrained_next_6h
```

Each horizon label is `1` when a relevant constraint binds in a future dispatch interval inside that window. The
current interval is excluded, so features for a row can remain current-or-past only.

Run the expanded scan:

```bash
python scripts/find_constrained_duids.py \
  --days 60 90 120 \
  --horizons-hours 1 3 6 \
  --max-duids 200 \
  --out artifacts/duid_constraint_scan_expanded.csv \
  --top-out artifacts/duid_constraint_scan_expanded_top.csv \
  --verbose
```

The scan writes one row per `DUID`/window/horizon. Pick the best model-training target from the ranked top CSV by
preferring rows where `recommended` is true, then higher `positive_intervals`, practical `positive_rate`, and recent
`last_positive_dispatch_interval`. Phase 2 trains against the selected horizon label with `--horizon-hours`.

### Phase 2: Horizon-Based ML Training

Phase 2 target:

```text
DUID: CATHROCK
connection_point_id: SCRK
days: 60
horizon_hours: 6
target: target_constrained_next_6h
```

The selected run generated:

```text
model_type: balanced_logistic_regression
total_samples: 17208
train_samples: 13766
test_samples: 3442
positive_rate: 0.013249651324965132
train_positive_rate: 0.016562545401714368
test_positive_rate: 0.0
average_precision: null
roc_auc: null
precision_at_top_5_percent: 0.0
```

The model is non-baseline. The chronological test window has no positive horizon events, so AP and ROC AUC are
reported as `null` rather than fabricated.

The trainer also records a supplemental positive held-out backtest. It chooses the latest 24-hour validation window
with positive labels and trains only on rows before that window:

```text
validation_start: 2026-04-22T22:20:00
validation_end: 2026-04-23T22:15:00
train_samples: 9723
train_positive_samples: 76
validation_samples: 288
validation_positive_samples: 152
validation_positive_rate: 0.5277777777777778
average_precision: 0.4725645845784202
roc_auc: 0.3285603715170279
precision_at_top_5_percent: 0.8
```

The positive-window AUC is weak, which is useful signal for the demo: time and weather features alone rank the event
window poorly, while the top 5% precision shows the fitted probabilities still concentrate some high-risk intervals.

Metrics and response artifacts:

```text
artifacts/cathrock_6h_metrics.json
artifacts/example_cathrock_6h_response.json
```

### Phase 2.5: Evaluation hardening

Phase 2.5 keeps the model story honest: this is a forecasting workflow demo, not a production-accurate constraint
forecaster. The normal chronological holdout contains no positive `CATHROCK` 6h horizon events, so AP and ROC AUC are
`null`. To still test ranking behavior around rare events, the repo includes event-window backtesting where each fold
trains only on rows strictly before the validation window.

Run:

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

Observed Phase 2.5 backtest summary:

```text
evaluated_folds: 2
skipped_folds: 1
mean_average_precision: 0.4292536031939571
mean_roc_auc: 0.5348134028244604
mean_precision_at_top_5_percent: 0.43333333333333335
mean_lift_at_top_5_percent: 0.8856345408976989
```

These metrics should not be described as strong model performance. They show that the pipeline can be evaluated without
future leakage and that some event-window ranking signal exists, while top-5% lift below 1.0 means the model is not
consistently concentrating the rare positive intervals.

Backtest artifacts:

```text
artifacts/cathrock_6h_backtest_summary.json
artifacts/cathrock_6h_backtest_folds.csv
artifacts/example_cathrock_6h_backtest_response.json
```

### Phase 3: Performance Improvement

Phase 3 adds leakage-guarded feature sets, model benchmarking, and a best-model bundle for the same `CATHROCK` 60-day
6h horizon target.

Run:

```bash
python scripts/benchmark_models.py \
  --duid CATHROCK \
  --days 60 \
  --horizon-hours 6 \
  --feature-sets time_only time_weather time_weather_dispatch_lag time_weather_constraint_lag \
  --model-types logistic_regression random_forest hist_gradient_boosting \
  --out artifacts/performance_benchmark.csv \
  --best-model-out artifacts/cathrock_6h_best_model.joblib \
  --report-out chatref/phase3_performance_report.md
```

Observed best setting:

```text
model: logistic_regression
feature_set: time_weather_dispatch_lag
evaluated_folds: 2
skipped_folds: 1
mean_average_precision: 0.6150641656145446
mean_roc_auc: 0.6186397911557198
mean_precision_at_top_5_percent: 0.6333333333333333
mean_lift_at_top_5_percent: 1.9449390521758945
```

Compared with the Phase 2.5 event-window AP of `0.4292536031939571`, the dispatch-lag feature set improved ranking in
this sparse-event backtest. This is still not a production accuracy claim: the normal chronological holdout has no
positive labels, event-window folds are few, and P5MIN/Predispatch, outage, regional demand, and interconnector inputs
remain outside this demo.

Phase 3 artifacts:

```text
artifacts/performance_benchmark.csv
artifacts/cathrock_6h_best_model.joblib
artifacts/cathrock_6h_best_model_metrics.json
artifacts/cathrock_6h_feature_importance.csv
chatref/phase3_performance_report.md
```

### 3. Run tests

The test suite is offline and uses synthetic AEMO-style ZIP payloads, mocked HTTP calls, and small in-memory training data.

```bash
python -m pytest
```

Coverage focus:

- notebook structure and cleared outputs;
- NEMWeb URL/download helpers and AEMO ZIP/table parsing;
- `DUID -> CONNECTIONPOINTID -> GENCONID` mapping and label construction;
- weather merge helpers;
- baseline ML training bundle creation;
- event-window backtest fold selection and safe rare-event metrics;
- forecast helper logic and FastAPI response/error paths.

### 4. Run query service

```bash
uvicorn app:app --reload
```

Online forecast query:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib"
```

Offline-safe demo forecast:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?model_path=artifacts/cathrock_6h_best_model.joblib&use_live_weather=false"
```

The API infers `horizon_hours=6` from the model bundle when the query parameter is omitted. By default it uses live
Open-Meteo forecast weather when available. If live weather is disabled or unavailable, the demo still returns a forecast
by leaving weather features missing and letting the model pipeline impute them. This keeps the GitHub demo reproducible
without internet access.

Explicit horizon also works:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h_best_model.joblib"
```

If an explicit horizon mismatches the model bundle, the API returns a clear `400` error instead of silently serving the
wrong horizon.

Example response shape:

```json
{
  "duid": "CATHROCK",
  "connection_point": "SCRK",
  "horizon_hours": 6,
  "model": {
    "model_type": "logistic_regression",
    "target_column": "target_constrained_next_6h",
    "positive_rate": 0.013249651324965132
  },
  "weather_source": "fallback_imputed",
  "weather_warning": "Open-Meteo forecast unavailable; weather features were imputed.",
  "relevant_genconids_count": 103,
  "forecast": [
    {
      "interval_start": "2026-05-20T00:00:00",
      "duid": "CATHROCK",
      "probability": 0.0114,
      "ml_constraint_probability": 0.0114,
      "ml_predicted_constrained": false,
      "risk_level": "very_low",
      "planned_constraints": [],
      "aemo_planned_relevant_constraints": []
    }
  ]
}
```

---

## Notebook path

Open:

```bash
jupyter notebook notebooks/aemo_constraint_forecast_demo.ipynb
```

The notebook walks through:

1. Target discovery and why `CATHROCK` was selected.
2. `DUID -> CONNECTIONPOINTID -> GENCONID` mapping.
3. Future-only `target_constrained_next_6h` label definition.
4. Training command and model metrics.
5. Event-window backtest summary.
6. API query examples with inferred and explicit horizon.
7. Limitations.

---

## Design choices

### Why Next Day Dispatch files?

Full MMSDM monthly dispatch tables can be hundreds of MB per table. For an internship demo, recent Next Day Dispatch files are a cleaner choice: they are small daily ZIPs and still include 5-minute unit dispatch plus constraint solutions for the previous trading day.

### Why logistic regression?

The first goal is a credible demo, not peak predictive performance. Balanced logistic regression is:

- fast;
- stable on small windows;
- explainable;
- easy to compare against a baseline.

A production extension can swap in gradient boosting, temporal convolution, sequence models, or quantile/probabilistic forecasting.

### What does “planned” mean here?

The service includes a lightweight planned/invoked view from `GENCONSET`, `GENCONSETINVOKE`, and `GENCONDATA`. This identifies relevant constraint sets active over future intervals where available. It is not a full P5MIN/Predispatch forecast-solution integration.

For a stronger production version, add:

- `P5MIN_CONSTRAINTSOLUTION` for near-term 5-minute forecast solutions;
- `PREDISPATCHCONSTRAINT` or new predispatch equivalent for longer-horizon forecast solutions;
- constraint-set outage descriptions and limit type metadata.

---

## Known limitations

1. **Network-vs-system classification is simplified.** This demo treats DUID-relevant generic constraints as the target class. A production build should classify network constraints more carefully.

2. **Geospatial metadata is tiny.**
   Phase 2 includes a vetted `CATHROCK` row in `data/duid_coordinates.csv`; production should maintain a complete
   DUID/station geospatial lookup.

3. **One model per DUID.** The service is intentionally narrow. Multi-DUID modelling would use a shared feature store and add DUID embeddings or categorical features.

4. **Short training window.** Recent rolling files are enough for a demo. Production should use long historical windows, outage context, seasonality, and backtesting.

5. **Forecast features are minimal.** Default query-time features are time and weather. Production should add AEMO forecast demand, regional/interconnector forecasts, UIGF, outage schedules, and P5MIN/Predispatch inputs.

6. **Evaluation remains sparse-event limited.**
   The chronological holdout has no positive `CATHROCK` 6h labels. Event-window folds are a transparent supplement, not
   proof of production-grade predictive accuracy.

---

## Suggested GitHub pitch

> Built a small AEMO public-data forecasting demo for DUID-level network constraint risk in the NEM. The system maps a generator DUID to relevant generic constraints, constructs labels from dispatch constraint outcomes, adds weather/time features, trains an interpretable ML classifier, and exposes a FastAPI endpoint returning planned/invoked constraints plus ML risk forecasts.

---

## Next production extensions

- Add P5MIN and Predispatch forecast-constraint solution ingestion.
- Add constraint taxonomy: thermal, voltage stability, transient stability, system strength, FCAS.
- Add long-run historical backtesting and grouped time-series cross-validation.
- Add DUID geospatial metadata and weather-grid matching.
- Add calibrated probabilities and threshold tuning by business objective.
- Add monitoring for data freshness, AEMO schema changes, and model drift.
