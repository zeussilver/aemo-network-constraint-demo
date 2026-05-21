# Resume Bullets

- Built a FastAPI demo service forecasting AEMO NEM constraint risk for a selected DUID using public NEMWeb dispatch and constraint data.
- Engineered future-only 6h horizon labels and leakage-guarded time, weather, and dispatch-lag features for sparse-event classification.
- Implemented DUID target discovery across public AEMO data to identify `CATHROCK` as a viable constrained-generator demo target after an initial zero-positive target failed.
- Benchmarked logistic regression, random forest, and histogram gradient boosting models with event-window backtests, improving mean AP from `0.429` to `0.615`.
- Packaged model artifacts, API examples, tests, a review notebook, and documentation into a GitHub-ready internship application project.
- Documented modelling limits clearly, including sparse event folds, no-positive chronological holdout, and production extensions such as P5MIN/Predispatch, outages, and regional demand.
