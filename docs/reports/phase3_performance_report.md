# Phase 3 Performance Report

Timestamp: `2026-05-19T15:22:50.994124+00:00`

## Summary

Phase 3 added named feature sets, sklearn model selection, event-window model benchmarking, best-model export, feature
importance output, and README documentation for the `CATHROCK` 60-day 6h horizon demo.

The best event-window setting was `logistic_regression` with `time_weather_dispatch_lag`. It improved mean AP versus
the Phase 2.5 event-window reference, but the result remains sparse-fold evidence rather than production accuracy.

## Files Changed

- `plan/phase3_execution_plan.md`
- `src/aemo_constraint_demo/features.py`
- `src/aemo_constraint_demo/train.py`
- `scripts/backtest_horizon_model.py`
- `scripts/benchmark_models.py`
- `tests/test_features.py`
- `tests/test_train.py`
- `tests/test_benchmark_models.py`
- `README.md`
- `artifacts/performance_benchmark.csv`
- `artifacts/cathrock_6h_best_model.joblib`
- `artifacts/cathrock_6h_best_model_metrics.json`
- `artifacts/cathrock_6h_feature_importance.csv`
- `docs/reports/phase3_performance_report.md`

## Commands Run

```bash
python -m pytest tests/test_features.py tests/test_train.py tests/test_benchmark_models.py -q
```

```bash
python -m pytest -q
```

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

## Benchmark Command

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

## Evaluated Settings

Feature sets: `time_only, time_weather, time_weather_dispatch_lag, time_weather_constraint_lag`

Model types: `logistic_regression, random_forest, hist_gradient_boosting`

## Benchmark Summary

```text
                 model                 feature_set  mean_average_precision  mean_precision_at_top_5_percent  mean_lift_at_top_5_percent  mean_roc_auc  evaluated_folds  skipped_folds
   logistic_regression                   time_only                0.482981                         0.033333                    0.125108      0.682404                2              1
         random_forest                   time_only                0.396194                         0.033333                    0.125108      0.495283                2              1
hist_gradient_boosting                   time_only                0.394556                         0.033333                    0.125108      0.217116                2              1
   logistic_regression                time_weather                0.429254                         0.433333                    0.885635      0.534813                2              1
         random_forest                time_weather                0.584995                         0.033333                    0.125108      0.656304                2              1
hist_gradient_boosting                time_weather                0.395546                         0.033333                    0.125108      0.218096                2              1
   logistic_regression   time_weather_dispatch_lag                0.615064                         0.633333                    1.944939      0.618640                2              1
         random_forest   time_weather_dispatch_lag                0.501488                         0.400000                    1.501299      0.552199                2              1
hist_gradient_boosting   time_weather_dispatch_lag                0.317554                         0.033333                    0.125108      0.098160                2              1
   logistic_regression time_weather_constraint_lag                0.428682                         0.233333                    0.505371      0.586851                2              1
         random_forest time_weather_constraint_lag                0.338498                         0.233333                    0.505371      0.115177                2              1
hist_gradient_boosting time_weather_constraint_lag                0.395546                         0.033333                    0.125108      0.218096                2              1
```

## Best Setting

- best_model: `logistic_regression`
- best_feature_set: `time_weather_dispatch_lag`
- mean_average_precision: `0.615064`
- mean_precision_at_top_5_percent: `0.633333`
- mean_lift_at_top_5_percent: `1.944939`
- mean_roc_auc: `0.618640`
- evaluated_folds: `2`
- skipped_folds: `1`

## Phase 2 Comparison

- Phase 2.5 reference AP: `0.429254`
- Phase 3 best AP: `0.615064`
- improved_over_phase2_5_event_window_ap: `true`

## Outputs

- benchmark_csv: `artifacts/performance_benchmark.csv`
- best_model_bundle: `artifacts/cathrock_6h_best_model.joblib`
- feature_importance_csv: `artifacts/cathrock_6h_feature_importance.csv`

## Feature Importance Summary

Top coefficient/permutation signals include:

- `dow_cos`
- `dow_sin`
- `wind_speed_10m`
- `month_sin`
- `hour_cos`
- `initialmw_rolling_mean_1h`

The dispatch-lag result supports the Phase 3 hypothesis that higher-signal operational features matter more than simply
switching to a more complex model.

## Test Result

```text
56 passed, 2 warnings in 1.81s
```

The warnings came from a small synthetic test fixture where `totalcleared_lag_6h` has no observed values.

## Limitations

- Event-window folds are sparse and selected around positive periods.
- The chronological holdout may still have no positive labels.
- Lagged constraint features improve backtests only if equivalent current/past operational signals are available at forecast time.
- P5MIN/Predispatch, outage, regional demand, and interconnector features remain future production extensions.
