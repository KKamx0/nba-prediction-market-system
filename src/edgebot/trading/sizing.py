"""Fractional Kelly sizing on a probability shrunk toward the market.

For a contract costing ``price`` that pays $1, the Kelly fraction is
``(p - price) / (1 - price)``. Two layers of caution are applied:

1. ``p`` is first blended toward the market (``shrink``), because the model's
   edge estimates are noisy and the market is usually right.
2. Only ``kelly_multiplier`` (default 0.25) of full Kelly is staked.

Hard caps (2% per position, 10% per night, ...) live in risk.py and apply
on top of whatever this returns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingConfig:
    kelly_multiplier: float = 0.25
    shrink_weight: float = 0.5  # weight on the model; the rest goes to the market


def kelly_fraction(p: float, price: float) -> float:
    if not 0 < price < 1:
        raise ValueError(f"price must be in (0, 1): {price}")
    if not 0 <= p <= 1:
        raise ValueError(f"probability must be in [0, 1]: {p}")
    return max(0.0, (p - price) / (1 - price))


def shrink(p_model: float, p_market: float, weight: float) -> float:
    return weight * p_model + (1 - weight) * p_market


def stake(
    *,
    p_model: float,
    p_market: float,
    price: float,
    bankroll: float,
    cfg: SizingConfig | None = None,
) -> float:
    """Dollars to stake before risk caps. ``price`` is the all-in price per contract."""
    cfg = cfg or SizingConfig()
    p = shrink(p_model, p_market, cfg.shrink_weight)
    return min(bankroll, cfg.kelly_multiplier * kelly_fraction(p, price) * bankroll)
