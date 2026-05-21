# Project Summary

This project is a compact AEMO public-data demo for forecasting DUID-level network constraint risk in the NEM. It maps a selected generator DUID to relevant generic constraints, builds future-only sparse-event labels from dispatch constraint outcomes, benchmarks small classifiers with leakage-guarded features, and exposes the selected model through a FastAPI endpoint. The final packaged target is `CATHROCK` with a 6h horizon.

## Technical Stack

- Python
- pandas
- scikit-learn
- FastAPI
- pytest
- Jupyter Notebook

## Data Sources

- AEMO NEMWeb MMSDM monthly standing-data archives
- AEMO Current Next Day Dispatch public files
- Open-Meteo historical and forecast weather APIs

## Model Approach

The demo uses `DUID -> CONNECTIONPOINTID -> GENCONID` mapping to identify constraints relevant to `CATHROCK`. It creates `target_constrained_next_6h`, a future-only label that marks whether any relevant constraint binds in the next 6 hours. Phase 3 benchmarks logistic regression, random forest, and histogram gradient boosting across time, weather, dispatch-lag, and constraint-lag feature sets. The selected setting is logistic regression with `time_weather_dispatch_lag` features.

## Evaluation Approach

The normal chronological holdout is reported honestly, including the fact that it contains no positive `CATHROCK` 6h labels. Event-window backtesting supplements this by training only on rows before rare positive validation windows. The selected model achieved mean event-window AP `0.615064`, precision@top 5% `0.633333`, lift@top 5% `1.944939`, and ROC AUC `0.618640` across two evaluated folds.

## Limitations

This is a workflow demo, not a production-accuracy forecast. Positive event windows are sparse, benchmark folds are few, the network-vs-system constraint taxonomy is simplified, and query-time features are intentionally minimal.

## Future Improvements

- Add P5MIN and Predispatch constraint solution ingestion.
- Add outage, regional demand, UIGF, and interconnector forecast features.
- Build a longer historical backtest with grouped time-series validation.
- Improve constraint taxonomy and network constraint classification.
- Calibrate probabilities and tune alert thresholds by business objective.
- Add data freshness and model drift monitoring.
