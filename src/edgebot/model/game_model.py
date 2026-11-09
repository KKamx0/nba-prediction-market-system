"""Win-probability models for the home team.

* ``MarketOnly``: Platt-calibrated sportsbook probability. The baseline every
  other model must beat on log loss, or the README says it doesn't.
* ``LogisticModel``: standardized features including ``market_logit``.
* ``LGBMModel``: gradient boosting with the market as an *offset*
  (``init_score = logit(market)``). Trees only learn corrections to the market,
  which is the "market as a strong prior" design in code.

``Calibrated`` wraps any model: it fits the model on the earlier part of the
training window and a Platt or isotonic calibrator on the most recent part.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from edgebot.model.features import MODEL_FEATURES

NON_MARKET_FEATURES = [f for f in MODEL_FEATURES if f != "market_logit"]


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-x))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _need_market(X: pd.DataFrame) -> None:
    if X["market_logit"].isna().any():
        raise ValueError("rows without a market price must be dropped before modeling")


class MarketOnly:
    name = "market"

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> MarketOnly:
        _need_market(X)
        self._lr = LogisticRegression(C=1e6).fit(X[["market_logit"]].to_numpy(), y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._lr.predict_proba(X[["market_logit"]].to_numpy())[:, 1]


class LogisticModel:
    name = "logistic"

    def __init__(self, C: float = 0.3) -> None:
        self.C = C

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> LogisticModel:
        _need_market(X)
        self._pipe = make_pipeline(StandardScaler(), LogisticRegression(C=self.C, max_iter=1000))
        self._pipe.fit(X[MODEL_FEATURES].to_numpy(), y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._pipe.predict_proba(X[MODEL_FEATURES].to_numpy())[:, 1]


@dataclass
class LGBMModel:
    name: str = "lightgbm"
    params: dict = field(
        default_factory=lambda: {
            "n_estimators": 150,
            "learning_rate": 0.03,
            "num_leaves": 7,
            "min_child_samples": 60,
            "reg_lambda": 10.0,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.8,
            "verbose": -1,
            "random_state": 0,
        }
    )

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> LGBMModel:
        import lightgbm as lgb

        _need_market(X)
        self._m = lgb.LGBMClassifier(**self.params)
        self._m.fit(X[NON_MARKET_FEATURES], y, init_score=X["market_logit"].to_numpy())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        raw = self._m.predict(X[NON_MARKET_FEATURES], raw_score=True)
        return _sigmoid(raw + X["market_logit"].to_numpy())


class Calibrated:
    """Fit ``base`` on the older (1 - frac) of the rows, a calibrator on the newest frac.

    Rows must be in chronological order.
    """

    def __init__(self, base, method: str = "platt", frac: float = 0.2) -> None:  # noqa: ANN001
        if method not in ("platt", "isotonic"):
            raise ValueError("method must be 'platt' or 'isotonic'")
        self.base, self.method, self.frac = base, method, frac
        self.name = f"{base.name}+{method}"

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> Calibrated:
        n_cal = max(int(len(X) * self.frac), 50)
        if len(X) - n_cal < 100:
            raise ValueError("not enough rows to fit and calibrate")
        Xf, yf = X.iloc[:-n_cal], y[:-n_cal]
        Xc, yc = X.iloc[-n_cal:], y[-n_cal:]
        self.base.fit(Xf, yf)
        raw = self.base.predict(Xc)
        if self.method == "platt":
            self._cal = LogisticRegression(C=1e6).fit(_logit(raw).reshape(-1, 1), yc)
        else:
            self._cal = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip").fit(
                raw, yc
            )
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.base.predict(X)
        if self.method == "platt":
            return self._cal.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]
        return self._cal.predict(raw)


def default_models() -> dict[str, callable]:
    """Factories, so every walk-forward refit starts from a clean model."""
    return {
        "market": lambda: MarketOnly(),
        "logistic+platt": lambda: Calibrated(LogisticModel(), "platt"),
        "lightgbm+platt": lambda: Calibrated(LGBMModel(), "platt"),
        "lightgbm+isotonic": lambda: Calibrated(LGBMModel(), "isotonic"),
    }
