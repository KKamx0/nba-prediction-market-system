"""Spec for sizing.py, written before the implementation.

Fractional Kelly (default 0.25) on a probability shrunk toward the market.
For a contract costing ``price`` that pays $1: f* = (p - price) / (1 - price).
"""

import pytest

from edgebot.trading.sizing import SizingConfig, kelly_fraction, shrink, stake


def test_full_kelly_for_binary_contract():
    assert kelly_fraction(0.60, 0.50) == pytest.approx(0.20)
    assert kelly_fraction(0.55, 0.40) == pytest.approx(0.25)


def test_no_bet_without_positive_edge():
    assert kelly_fraction(0.50, 0.50) == 0
    assert kelly_fraction(0.40, 0.50) == 0


@pytest.mark.parametrize("price", [0.0, 1.0, -0.1, 1.5])
def test_invalid_prices_rejected(price):
    with pytest.raises(ValueError):
        kelly_fraction(0.6, price)


def test_shrink_blends_toward_market():
    assert shrink(0.70, 0.50, weight=0.5) == pytest.approx(0.60)
    assert shrink(0.70, 0.50, weight=1.0) == pytest.approx(0.70)
    assert shrink(0.70, 0.50, weight=0.0) == pytest.approx(0.50)


def test_stake_uses_quarter_kelly_on_shrunk_probability():
    cfg = SizingConfig()  # 0.25 Kelly, 0.5 shrink weight
    assert cfg.kelly_multiplier == 0.25
    # model 0.70, market 0.50 -> shrunk 0.60; all-in price 0.50 -> f* = 0.2
    # stake = 0.25 * 0.2 * 1000 = 50 dollars
    assert stake(p_model=0.70, p_market=0.50, price=0.50, bankroll=1000, cfg=cfg) == pytest.approx(
        50
    )


def test_stake_is_zero_when_shrinking_removes_the_edge():
    # model sees edge, but blended toward the market it's gone
    assert stake(p_model=0.56, p_market=0.44, price=0.51, bankroll=1000) == 0


def test_stake_never_negative_or_above_bankroll():
    assert stake(p_model=0.99, p_market=0.98, price=0.01, bankroll=100) <= 100
    assert stake(p_model=0.10, p_market=0.10, price=0.50, bankroll=100) == 0
