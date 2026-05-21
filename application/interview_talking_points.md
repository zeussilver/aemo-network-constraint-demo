# Interview Talking Points

## Why the First OAKEY1SF Attempt Failed

The first target was useful for proving that ingestion, mapping, training, and API plumbing worked, but it had too few positive constraint labels for a meaningful classifier. The model would mostly learn a constant or near-constant rare-event baseline, so reporting strong accuracy would have been misleading.

## Why Target Discovery Was Needed

Sparse-event forecasting depends heavily on finding a target with enough positive examples. The target discovery scripts scan candidate DUIDs, connection-point mappings, relevant GENCONIDs, and horizon labels so the demo can pick a target that is still rare but learnable enough for a reviewable workflow.

## Why Horizon Labels Are Better Than 5-Minute Labels

A same-interval 5-minute binding label is very sparse and is not aligned with a useful forecast product. The future-only 6h label asks whether any relevant constraint will bind in the next 6 hours, excludes the current interval, and drops incomplete tail rows so labels do not leak future knowledge into features.

## Why Dispatch-Lag Features Helped

Time and weather features alone describe broad operating conditions, but dispatch-lag features add current-or-past unit operating context such as output, availability, UIGF, and ramp rates. In Phase 3, those lagged signals improved event-window ranking versus the Phase 2.5 time/weather baseline.

## Why Results Should Not Be Overstated

The best setting improved sparse-event ranking in two evaluated event-window folds, but the chronological holdout has no positive events and the validation set is still small. The right claim is that the project demonstrates an honest end-to-end workflow, not that it is production accurate.

## Production Extensions

The next production steps would be to add P5MIN and Predispatch constraint solution data, outage and constraint-set context, regional demand and interconnector forecasts, longer historical backtests, probability calibration, monitoring, and a stronger taxonomy for separating network constraints from system constraints.
