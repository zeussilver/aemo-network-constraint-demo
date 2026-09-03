# Lead-time bucket models

Target: `CATHROCK`, 60 days, horizon `6h`, feature set
`time_weather_dispatch_lag`, model `logistic_regression`.

Each bucket is a disjoint window inside the horizon, labelled
`target_constrained_lead_Xh(t) = 1 if a relevant constraint binds in (t+(X-1)h, t+Xh]`.
Features stay origin-anchored, so every bucket is scored from the same observed state.

## Per-bucket event-window backtest

| Bucket | Window | Positives | Positive rate | Folds | Mean AP | Mean ROC AUC | Mean lift@top-5% |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1h | (t+0h, t+1h] | 48 | 0.0028 | 2 | 0.1276 | 0.5699 | 0.5667 |
| 2h | (t+1h, t+2h] | 48 | 0.0028 | 2 | 0.1664 | 0.5350 | 0.5667 |
| 3h | (t+2h, t+3h] | 48 | 0.0028 | 2 | 0.0943 | 0.3747 | 0.5667 |
| 4h | (t+3h, t+4h] | 48 | 0.0028 | 2 | 0.1519 | 0.6248 | 0.5667 |
| 5h | (t+4h, t+5h] | 48 | 0.0028 | 2 | 0.1370 | 0.6554 | 0.5667 |
| 6h | (t+5h, t+6h] | 48 | 0.0028 | 2 | 0.1514 | 0.6776 | 0.5667 |

## Cumulative horizon reference

The same frame, labelled `target_constrained_next_6h` (bind anywhere in the horizon):

```text
evaluated_folds: 2
mean_average_precision: 0.6048
mean_roc_auc: 0.6085
mean_lift_at_top_5_percent: 1.8182
```

## Reading this honestly

Bucket labels are sparser than the cumulative label by construction - a 6h horizon
positive becomes a positive in only the bucket that contains the event - so per-bucket
folds are fewer and the metrics are noisier. Buckets whose folds were all skipped report
`n/a` rather than a fabricated number. This is a demonstration that the forward curve can
be produced without train/serve skew, not evidence of production-grade lead-time accuracy.

Generated at 2026-09-03T02:47:01.352146+00:00.
