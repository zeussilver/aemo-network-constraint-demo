# AEMO DUID Network Constraint Forecast Demo

A compact, GitHub-ready demo for a Proa Analytics internship application.

The demo answers a narrow but realistic question:

> For a given AEMO Dispatchable Unit ID (`DUID`), forecast the next 1–72 hours in which network-related generic constraints are likely to bind.

It uses AEMO public NEMWeb data for dispatch labels and constraint mapping, adds weather/time features, trains a small ML classifier, and exposes a queryable FastAPI endpoint.

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
   - Features: time-of-day, day-of-week, month, weekend flag, and weather.
   - Default model: balanced logistic regression for speed and explainability.

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
├── notebooks/
│   └── aemo_constraint_forecast_demo.ipynb
├── tests/
│   ├── test_aemo_client.py
│   ├── test_api.py
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

### 2. Train the Phase 2 demo model

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
    "roc_auc": null
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

Metrics and response artifacts:

```text
artifacts/cathrock_6h_metrics.json
artifacts/example_cathrock_6h_response.json
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
- forecast helper logic and FastAPI response/error paths.

### 4. Run query service

```bash
uvicorn app:app --reload
```

Query:

```bash
curl "http://127.0.0.1:8000/forecast/CATHROCK?horizon_hours=6&model_path=artifacts/cathrock_6h.joblib"
```

Example response shape:

```json
{
  "duid": "CATHROCK",
  "connection_point": "SCRK",
  "horizon_hours": 6,
  "model": {
    "model_type": "balanced_logistic_regression",
    "target_column": "target_constrained_next_6h",
    "positive_rate": 0.013249651324965132
  },
  "relevant_genconids_count": 103,
  "forecast": [
    {
      "interval_start": "2026-05-20T00:00:00",
      "duid": "CATHROCK",
      "probability": 0.0124,
      "ml_constraint_probability": 0.0124,
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

1. Standing-data downloads.
2. `DUID -> CONNECTIONPOINTID -> GENCONID` mapping.
3. Next Day Dispatch parsing.
4. Label construction.
5. Weather merge.
6. Chronological train/test split.
7. Model training and metric interpretation.
8. API query example.

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

1. **Network-vs-system classification is simplified.**
   This demo treats DUID-relevant generic constraints as the target class. A production build should classify network constraints more carefully.

2. **Geospatial metadata is tiny.**
   Phase 2 includes a vetted `CATHROCK` row in `data/duid_coordinates.csv`; production should maintain a complete
   DUID/station geospatial lookup.

3. **One model per DUID.**
   The service is intentionally narrow. Multi-DUID modelling would use a shared feature store and add DUID embeddings or categorical features.

4. **Short training window.**
   Recent rolling files are enough for a demo. Production should use long historical windows, outage context, seasonality, and backtesting.

5. **Forecast features are minimal.**
   Default query-time features are time and weather. Production should add AEMO forecast demand, regional/interconnector forecasts, UIGF, outage schedules, and P5MIN/Predispatch inputs.

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
