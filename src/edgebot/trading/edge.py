"""Edge against the executable price, after fees and slippage.

All prices are dollars per contract that pays $1 if the side wins.

    edge = p_win - (vwap at our size + taker fee per contract + slippage buffer)

The taker fee follows the published schedule (docs.polymarket.us/fees):
``Fee = theta * C * p * (1 - p)``, rounded to cents with banker's rounding.
Edge is computed as if we cross the spread (taker), which is the
conservative case; a resting order that fills as a maker pays no fee.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

Levels = Sequence[tuple[float, float]]  # (price, qty), best first


@dataclass(frozen=True)
class EdgeConfig:
    min_edge: float = 0.04  # 4 points after fees and slippage
    min_liquidity_usd: float = 200.0  # dollars available at prices that still clear min_edge
    disagreement_cap: float = 0.15  # |model - market| above this -> no bet, flag for review
    slippage: float = 0.005  # buffer for the book moving between quote and fill


def _levels(levels) -> list[tuple[float, float]]:  # noqa: ANN001
    out = []
    for lv in levels:
        price, qty = (lv.price, lv.qty) if hasattr(lv, "price") else lv
        out.append((float(price), float(qty)))
    return out


def walk_book(
    levels, qty: float, limit_price: float | None = None  # noqa: ANN001
) -> tuple[float, float | None, list[tuple[float, float]]]:
    """Fill up to ``qty`` against best-first ask levels, never above ``limit_price``."""
    remaining, fills = qty, []
    for price, avail in _levels(levels):
        if remaining <= 0 or (limit_price is not None and price > limit_price + 1e-12):
            break
        take = min(avail, remaining)
        fills.append((price, take))
        remaining -= take
    filled = sum(q for _, q in fills)
    vwap = sum(p * q for p, q in fills) / filled if filled else None
    return filled, vwap, fills


def taker_fee_per_contract(price: float, theta: float) -> float:
    return theta * price * (1 - price)


def fee_cents(price: float, qty: float, theta: float) -> float:
    """Fee charged on one fill, rounded half-to-even to the cent."""
    exact = (
        Decimal(str(theta)) * Decimal(str(qty)) * Decimal(str(price)) * (1 - Decimal(str(price)))
    )
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class EdgeQuote:
    p_win: float
    qty: float
    vwap: float
    fee_per_contract: float
    slippage: float
    worst_price: float  # highest level touched; the limit price for the order

    @property
    def all_in_price(self) -> float:
        return self.vwap + self.fee_per_contract + self.slippage

    @property
    def edge(self) -> float:
        return self.p_win - self.all_in_price

    @property
    def cost(self) -> float:
        return self.qty * self.all_in_price


def quote_side(
    p_win: float, asks, qty: float, theta: float, cfg: EdgeConfig | None = None  # noqa: ANN001
) -> EdgeQuote | None:
    """Edge for buying ``qty`` contracts now. None if the book can't fill it."""
    cfg = cfg or EdgeConfig()
    filled, vwap, fills = walk_book(asks, qty)
    if vwap is None or filled + 1e-9 < qty:
        return None
    fee = sum(taker_fee_per_contract(p, theta) * q for p, q in fills) / filled
    return EdgeQuote(
        p_win=p_win,
        qty=filled,
        vwap=vwap,
        fee_per_contract=fee,
        slippage=cfg.slippage,
        worst_price=max(p for p, _ in fills),
    )


def max_qty_with_edge(
    p_win: float, asks, theta: float, cfg: EdgeConfig | None = None
) -> float:  # noqa: ANN001
    """Contracts available at levels whose own all-in price still clears ``min_edge``."""
    cfg = cfg or EdgeConfig()
    total = 0.0
    for price, avail in _levels(asks):
        all_in = price + taker_fee_per_contract(price, theta) + cfg.slippage
        if p_win - all_in < cfg.min_edge:
            break
        total += avail
    return total
