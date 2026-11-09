import numpy as np
import pandas as pd
import pytest

from edgebot.model.features import MODEL_FEATURES
from edgebot.model.game_model import (
    Calibrated,
    LGBMModel,
    LogisticModel,
    MarketOnly,
    default_models,
)
from edgebot.model.metrics import log_loss


def _frame(n=4000, signal=0.0, market_noise=0.3, seed=0):
    """Synthetic rows: true logit = strength signal; market sees it with noise."""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({f: rng.normal(size=n) for f in MODEL_FEATURES})
    true_logit = 0.2 + 0.8 * rng.normal(size=n) + signal * X["strength_diff"]
    X["market_logit"] = true_logit - signal * X["strength_diff"] + rng.normal(0, market_noise, n)
    if signal == 0:
        X["market_logit"] = true_logit + rng.normal(0, market_noise, n)
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-true_logit))).astype(int)
    return X, y


def test_market_only_is_close_to_the_market_when_market_is_efficient():
    X, y = _frame(signal=0.0, market_noise=0.0)
    p = MarketOnly().fit(X, y).predict(X)
    market = 1 / (1 + np.exp(-X["market_logit"]))
    assert np.abs(p - market).mean() < 0.02


def test_models_refuse_rows_without_a_market():
    X, y = _frame(n=500)
    X.loc[0, "market_logit"] = np.nan
    for model in (MarketOnly(), LogisticModel(), LGBMModel()):
        with pytest.raises(ValueError, match="market"):
            model.fit(X, y)


def test_feature_models_beat_market_when_the_market_misses_signal():
    X, y = _frame(n=8000, signal=0.6, seed=1)
    tr, te = slice(0, 6000), slice(6000, None)
    ll = {}
    for name, factory in default_models().items():
        m = factory().fit(X.iloc[tr], y[tr])
        ll[name] = log_loss(y[te], m.predict(X.iloc[te]))
    assert ll["logistic+platt"] < ll["market"] - 0.01
    assert ll["lightgbm+platt"] < ll["market"]


def test_lightgbm_offset_stays_near_market_when_features_are_noise():
    X, y = _frame(n=6000, signal=0.0, market_noise=0.0, seed=2)
    p = LGBMModel().fit(X.iloc[:5000], y[:5000]).predict(X.iloc[5000:])
    market = 1 / (1 + np.exp(-X["market_logit"].iloc[5000:]))
    # boosting starts from the market and has little reason to move off it
    assert np.abs(p - market).mean() < 0.04


def test_calibration_wrapper_validates_inputs():
    with pytest.raises(ValueError):
        Calibrated(MarketOnly(), method="magic")
    X, y = _frame(n=120)
    with pytest.raises(ValueError, match="not enough"):
        Calibrated(MarketOnly()).fit(X, y)
    X, y = _frame(n=2000)
    for method in ("platt", "isotonic"):
        p = Calibrated(LogisticModel(), method).fit(X, y).predict(X)
        assert ((p > 0) & (p < 1)).all()
