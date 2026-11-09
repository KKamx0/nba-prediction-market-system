"""Spec for edge.py, written before the implementation.

Edge = calibrated model probability - executable price at our size, after
taker fees and a slippage buffer. Prices are dollars per $1-payout contract.
"""

import pytest

from edgebot.trading.edge import (
    EdgeConfig,
    fee_cents,
    max_qty_with_edge,
    quote_side,
    taker_fee_per_contract,
    walk_book,
)

ASKS = [(0.48, 100.0), (0.49, 200.0), (0.51, 300.0)]


def test_walk_book_vwap_across_levels():
    filled, vwap, fills = walk_book(ASKS, 250)
    assert filled == 250
    assert vwap == pytest.approx((0.48 * 100 + 0.49 * 150) / 250)
    assert fills == [(0.48, 100.0), (0.49, 150.0)]


def test_walk_book_respects_limit_price_and_depth():
    filled, vwap, _ = walk_book(ASKS, 1000, limit_price=0.49)
    assert filled == 300
    assert vwap == pytest.approx((0.48 * 100 + 0.49 * 200) / 300)
    assert walk_book([], 10) == (0.0, None, [])


def test_taker_fee_matches_published_formula():
    # docs.polymarket.us/fees: Fee = theta * C * p * (1 - p)
    assert taker_fee_per_contract(0.50, 0.095) == pytest.approx(0.02375)
    assert taker_fee_per_contract(0.10, 0.095) == pytest.approx(0.00855)
    # 100 contracts at 0.50 with the 0.0695 tier = $1.7375 -> banker's rounding to $1.74
    assert fee_cents(0.50, 100, 0.0695) == pytest.approx(1.74)
    # exact half cent rounds to even
    assert fee_cents(0.50, 2, 0.05) == pytest.approx(0.02)  # 0.025 -> 0.02


def test_edge_is_after_fees_and_slippage():
    cfg = EdgeConfig(slippage=0.005)
    q = quote_side(p_win=0.60, asks=ASKS, qty=100, theta=0.095, cfg=cfg)
    fee = 0.095 * 0.48 * 0.52
    assert q.vwap == pytest.approx(0.48)
    assert q.all_in_price == pytest.approx(0.48 + fee + 0.005)
    assert q.edge == pytest.approx(0.60 - (0.48 + fee + 0.005))


def test_quote_returns_none_without_enough_liquidity():
    assert quote_side(p_win=0.60, asks=ASKS, qty=10_000, theta=0.095) is None


def test_bigger_size_walks_deeper_and_shrinks_edge():
    small = quote_side(p_win=0.60, asks=ASKS, qty=50, theta=0.095)
    big = quote_side(p_win=0.60, asks=ASKS, qty=500, theta=0.095)
    assert big.vwap > small.vwap
    assert big.edge < small.edge


def test_max_qty_with_edge_stops_where_marginal_edge_falls_below_minimum():
    cfg = EdgeConfig(min_edge=0.04, slippage=0.0)
    # at p=0.58: level 0.48 has edge 0.58-0.48-fee(0.0237) = 0.076 ok
    #            level 0.49 has edge ~0.066 ok, level 0.51 has edge ~0.046 ok
    assert max_qty_with_edge(0.58, ASKS, 0.095, cfg) == 600
    # at p=0.56: 0.49 level edge = 0.56-0.5137 = 0.046 ok; 0.51 level 0.026 -> excluded
    assert max_qty_with_edge(0.56, ASKS, 0.095, cfg) == 300
    # at p=0.55: 0.49 level edge = 0.036 < 0.04 -> only the first level qualifies
    assert max_qty_with_edge(0.55, ASKS, 0.095, cfg) == 100
    assert max_qty_with_edge(0.40, ASKS, 0.095, cfg) == 0


def test_config_defaults_match_the_spec():
    cfg = EdgeConfig()
    assert cfg.min_edge == 0.04
    assert cfg.disagreement_cap == 0.15
    assert cfg.min_liquidity_usd > 0
