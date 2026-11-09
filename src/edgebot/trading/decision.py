"""Turn model probabilities and a live book into bet / pass / review.

Order of checks, cheapest and most conservative first:

1. Market must be open with quotes on the side we'd buy.
2. **Disagreement cap**: if model and market differ by more than 15 points on
   either side, don't bet; flag for review. A gap that large usually means stale
   data or a missed injury, not an edge.
3. Pick the side with the better top-of-book edge; it must clear ``min_edge``
   after fees and slippage.
4. **Availability**: if anyone in the game is questionable, the edge must hold
   in both the "everyone plays" and "everyone sits" scenarios.
5. **Liquidity**: enough dollars at prices that still clear ``min_edge``.
6. **Size**: fractional Kelly on a market-shrunk probability, then shrink the
   order until the edge still clears ``min_edge`` at that size.

Risk limits (risk.py) are applied afterwards by the runner and can only shrink
or block the bet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from edgebot.ingest.polymarket import Moneyline, OrderBook
from edgebot.trading.edge import EdgeConfig, EdgeQuote, max_qty_with_edge, quote_side
from edgebot.trading.sizing import SizingConfig, stake


@dataclass(frozen=True)
class GameView:
    market: Moneyline
    book: OrderBook
    p_home: float  # calibrated model probability, expected availability
    p_home_play: float  # every uncertain player plays
    p_home_sit: float  # every uncertain player sits
    has_uncertainty: bool
    p_home_sportsbook: float | None = None


@dataclass
class Decision:
    action: str  # 'bet' | 'pass' | 'review'
    reasons: list[str] = field(default_factory=list)
    side_team: str | None = None
    long_side: bool | None = None
    p_side: float | None = None
    p_side_play: float | None = None
    p_side_sit: float | None = None
    market_mid: float | None = None
    sportsbook: float | None = None
    quote: EdgeQuote | None = None

    @property
    def contracts(self) -> float:
        return self.quote.qty if self.quote else 0.0

    @property
    def stake(self) -> float:
        return self.quote.cost if self.quote else 0.0


def _side_mid(book: OrderBook, long_side: bool) -> float | None:
    mid = book.mid()
    if mid is None:
        return None
    return mid if long_side else 1 - mid


def _floor_qty(qty: float) -> float:
    return math.floor(qty * 100) / 100  # 0.01-contract granularity


def decide(
    view: GameView,
    *,
    bankroll: float,
    theta: float,
    edge_cfg: EdgeConfig | None = None,
    sizing_cfg: SizingConfig | None = None,
) -> Decision:
    ecfg = edge_cfg or EdgeConfig()
    m, book = view.market, view.book
    if not book.is_open:
        return Decision("pass", [f"market not open ({book.state})"])

    sides = []
    for team in (m.home, m.away):
        is_home = team == m.home
        long_side = team == m.long_team
        p = view.p_home if is_home else 1 - view.p_home
        mid = _side_mid(book, long_side)
        sb = view.p_home_sportsbook
        sides.append(
            Decision(
                "pass",
                side_team=team,
                long_side=long_side,
                p_side=p,
                p_side_play=view.p_home_play if is_home else 1 - view.p_home_play,
                p_side_sit=view.p_home_sit if is_home else 1 - view.p_home_sit,
                market_mid=mid,
                sportsbook=None if sb is None else (sb if is_home else 1 - sb),
            )
        )

    if any(s.market_mid is None for s in sides):
        return Decision("pass", ["no two-sided market"])
    gap = abs(sides[0].p_side - sides[0].market_mid)
    if gap > ecfg.disagreement_cap:
        d = sides[0]
        d.action = "review"
        d.reasons = [
            f"model {d.p_side:.3f} vs market {d.market_mid:.3f} for {d.side_team}: "
            f"{gap:.1%} gap exceeds {ecfg.disagreement_cap:.0%} cap; check data and injuries"
        ]
        return d

    for s in sides:
        s.quote = quote_side(s.p_side, book.asks_for(s.long_side)[:1], 1.0, theta, ecfg)
    best = max(sides, key=lambda s: s.quote.edge if s.quote else -1)
    if best.quote is None:
        return Decision("pass", ["no asks on either side"])
    if best.quote.edge < ecfg.min_edge:
        best.action, best.reasons = "pass", [
            f"best edge {best.quote.edge:.1%} on {best.side_team} below {ecfg.min_edge:.0%}"
        ]
        return best

    asks = book.asks_for(best.long_side)
    if view.has_uncertainty:
        for label, p in (("plays", best.p_side_play), ("sits", best.p_side_sit)):
            q = quote_side(p, asks[:1], 1.0, theta, ecfg)
            if q is None or q.edge < ecfg.min_edge:
                best.action = "pass"
                best.reasons = [
                    f"edge doesn't survive the scenario where questionable players {label} "
                    f"(p={p:.3f}); waiting for availability news"
                ]
                return best

    max_qty = max_qty_with_edge(best.p_side, asks, theta, ecfg)
    top_price = asks[0][0] if isinstance(asks[0], tuple) else asks[0].price
    if max_qty * top_price < ecfg.min_liquidity_usd:
        best.action = "pass"
        best.reasons = [f"thin book: ${max_qty * top_price:.0f} at prices that keep the edge"]
        return best

    dollars = stake(
        p_model=best.p_side,
        p_market=best.market_mid,
        price=best.quote.all_in_price,
        bankroll=bankroll,
        cfg=sizing_cfg,
    )
    qty = _floor_qty(min(dollars / best.quote.all_in_price, max_qty))
    quote = None
    while qty >= 0.01:
        quote = quote_side(best.p_side, asks, qty, theta, ecfg)
        if quote is not None and quote.edge >= ecfg.min_edge:
            break
        qty = _floor_qty(qty / 2)
        quote = None
    if quote is None:
        best.action = "pass"
        best.reasons = ["Kelly stake on the market-shrunk probability is zero"]
        return best

    best.quote = quote
    best.action = "bet"
    best.reasons = [
        f"edge {quote.edge:.1%} on {best.side_team} at {quote.vwap:.3f} "
        f"(+{quote.fee_per_contract:.3f} fee), {qty:g} contracts"
    ]
    return best
