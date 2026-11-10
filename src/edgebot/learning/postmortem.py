"""One verdict per settled bet: was it a good bet, a bad bet, or bad luck?

Verdicts, checked in order:

* ``availability miss``  a player we projected for 15+ minutes didn't play, or
                         one we projected near 0 played 15+ (names recorded)
* ``beaten by the market`` CLV below -0.02: the price moved hard against us
* ``good bet, won`` / ``good bet, lost (variance)``  CLV >= 0
* ``lucky win`` / ``bad bet, lost``                  CLV < 0

"Good bet, lost" is the important one to read correctly: the bot did its job
and the ball didn't go in. Nothing should be learned from those beyond CLV.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from edgebot.timeutil import to_iso

SCRATCH_MIN = 15.0  # projected minutes that make a scratch a real surprise


def availability_surprises(store, game_date: str, home: str, away: str, before: str) -> list[str]:
    game = store.query(
        "SELECT game_id FROM games WHERE game_date = ? AND home = ? AND away = ?",
        (game_date, home, away),
    )
    if not game:
        return []
    gid = game[0]["game_id"]
    projections = store.query(
        """
        SELECT mp.team, mp.player_id, mp.player_name, mp.projected FROM minutes_projections mp
        JOIN (
            SELECT team, player_id, MAX(known_at) AS t FROM minutes_projections
            WHERE game_date = ? AND team IN (?, ?) AND known_at <= ?
            GROUP BY team, player_id
        ) last ON last.team = mp.team AND last.player_id = mp.player_id AND last.t = mp.known_at
        WHERE mp.game_date = ?
        """,
        (game_date, home, away, before, game_date),
    )
    actual = {
        r["player_id"]: r["minutes"]
        for r in store.query(
            "SELECT player_id, minutes FROM player_games WHERE game_id = ?", (gid,)
        )
    }
    out = []
    for p in projections:
        played = actual.get(p["player_id"], 0.0)
        if p["projected"] >= SCRATCH_MIN and played == 0:
            out.append(f"{p['player_name']} ({p['team']}) projected {p['projected']:.0f} min, sat")
        elif p["projected"] < 1 and played >= SCRATCH_MIN:
            out.append(f"{p['player_name']} ({p['team']}) projected out, played {played:.0f} min")
    return out


def verdict(clv: float | None, won: bool, surprises: list[str]) -> str:
    if surprises:
        return "availability miss"
    if clv is None:
        return "won (no closing price)" if won else "lost (no closing price)"
    if clv < -0.02:
        return "beaten by the market"
    if clv >= 0:
        return "good bet, won" if won else "good bet, lost (variance)"
    return "lucky win" if won else "bad bet, lost"


def review_settled(store, now: datetime) -> list[dict]:
    """Review every settled position that hasn't been reviewed yet."""
    rows = store.query("""
        SELECT p.* FROM positions p LEFT JOIN bet_reviews b ON b.position_id = p.id
        WHERE p.settled_at IS NOT NULL AND b.position_id IS NULL
        """)
    out = []
    for p in rows:
        clv = None if p["closing_price"] is None else p["closing_price"] - p["cost"] / p["qty"]
        before = p["tip_utc"] or p["opened_at"]
        surprises = availability_surprises(store, p["game_date"], p["home"], p["away"], before)
        v = verdict(clv, bool(p["won"]), surprises)
        notes = f"{p['side_team']} {'won' if p['won'] else 'lost'}, pnl {p['pnl']:+.2f}"
        with store.conn:
            store.conn.execute(
                "INSERT INTO bet_reviews (position_id, known_at, verdict, clv, surprises, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p["id"], to_iso(now), v, clv, json.dumps(surprises), notes),
            )
        out.append({"position_id": p["id"], "verdict": v, "clv": clv, "surprises": surprises})
    return out


def verdict_counts(store, mode: str = "paper") -> Counter:
    rows = store.query(
        "SELECT b.verdict FROM bet_reviews b JOIN positions p ON p.id = b.position_id WHERE p.mode = ?",
        (mode,),
    )
    return Counter(r["verdict"] for r in rows)
