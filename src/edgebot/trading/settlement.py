"""Settle positions once results are known, and record closing line value.

CLV for a position = (side mid in the last snapshot at or before tip) minus
(our average fill price). Positive CLV means we bought cheaper than where the
market closed, which is the best available evidence of real edge. Over a small
sample, ROI is mostly noise.
"""

from __future__ import annotations

from datetime import datetime

from edgebot.timeutil import parse_iso, to_iso


def closing_mid(store, market_slug: str, long_side: bool, tip_utc: str | None) -> float | None:
    if tip_utc is None:
        return None
    snap = store.latest_market_snapshot_as_of(market_slug, parse_iso(tip_utc))
    if snap is None or snap["best_bid"] is None or snap["best_ask"] is None:
        return None
    mid = (snap["best_bid"] + snap["best_ask"]) / 2
    return mid if long_side else 1 - mid


def settle_positions(store, now: datetime, on_settle=None) -> list[dict]:
    """Settle every open position whose game has a known result at ``now``."""
    open_rows = store.query(
        """
        SELECT p.*, r.home_pts, r.away_pts FROM positions p
        JOIN games g ON g.game_date = p.game_date AND g.home = p.home AND g.away = p.away
        JOIN game_results r ON r.game_id = g.game_id
        WHERE p.settled_at IS NULL AND r.known_at <= ?
        """,
        (to_iso(now),),
    )
    settled = []
    for p in open_rows:
        winner = p["home"] if p["home_pts"] > p["away_pts"] else p["away"]
        won = p["side_team"] == winner
        payout = p["qty"] if won else 0.0
        pnl = payout - p["cost"] - p["fees"]
        close = closing_mid(store, p["market_slug"], bool(p["long_side"]), p["tip_utc"])
        with store.conn:
            store.conn.execute(
                """
                UPDATE positions SET settled_at = ?, won = ?, payout = ?, pnl = ?,
                                     closing_price = COALESCE(closing_price, ?)
                WHERE id = ?
                """,
                (to_iso(now), int(won), payout, pnl, close, p["id"]),
            )
        row = {**dict(p), "won": won, "payout": payout, "pnl": pnl, "closing_price": close}
        settled.append(row)
        if on_settle:
            on_settle(row)
    return settled
