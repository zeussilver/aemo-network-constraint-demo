"""Small sklearn-compatible model helpers."""
from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin


class ConstantProbabilityClassifier(BaseEstimator, ClassifierMixin):
    """Two-column predict_proba baseline for one-class training windows."""

    def __init__(self, probability: float = 0.0) -> None:
        self.probability = probability

    def fit(self, X, y=None):  # noqa: ANN001, ANN201 - sklearn estimator API
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):  # noqa: ANN001, ANN201 - sklearn estimator API
        positive = np.full(len(X), float(self.probability))
        return np.column_stack([1 - positive, positive])

    def predict(self, X):  # noqa: ANN001, ANN201 - sklearn estimator API
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)
