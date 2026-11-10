"""Which kinds of bets work, learned from closing line value.

Each settled bet is described along a few dimensions (the team we backed, the
opponent, home/away, favorite/underdog, weekday, back-to-back). For every
dimension value the mean CLV is shrunk toward zero with ``prior_n`` pseudo-bets:

    estimate = n * mean / (n + prior_n)
    interval = estimate +/- z * sd / sqrt(n + prior_n)

and turned into an action:

* ``avoid``      upper bound below ``avoid_below``: don't bet this segment
* ``raise_edge`` upper bound below 0: require ``raise_mult`` x the usual edge
* ``favor``      lower bound above 0: require ``favor_mult`` x the edge (floored)
* ``neutral`` / ``insufficient``: no change

Rules only change the *minimum edge*. Probabilities, sizing caps, the
disagreement cap and risk limits are untouched.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from edgebot.timeutil import to_iso

DIMENSIONS = ("team", "opponent", "venue", "role", "weekday", "rest")


@dataclass(frozen=True)
class LearningConfig:
    prior_n: float = 30.0  # pseudo-bets of "no effect" every segment starts with
    min_n: int = 20  # real bets needed before a segment can act
    z: float = 1.645  # 90% two-sided interval
    avoid_below: float = -0.01  # CLV per contract
    raise_mult: float = 1.5
    favor_mult: float = 0.85
    min_edge_floor: float = 0.03
    max_mult: float = 2.0


@dataclass(frozen=True)
class SegmentStat:
    dimension: str
    value: str
    n: int
    mean_clv: float
    estimate: float
    lower: float
    upper: float
    pnl: float
    action: str
    multiplier: float


def context(
    *, side_team: str, home: str, away: str, game_date: str, price: float, back_to_back: bool
) -> dict[str, str]:
    """The segment values one bet belongs to. Used for learning and for live decisions."""
    return {
        "team": side_team,
        "opponent": away if side_team == home else home,
        "venue": "home" if side_team == home else "away",
        "role": "favorite" if price >= 0.5 else "underdog",
        "weekday": date.fromisoformat(game_date).strftime("%A"),
        "rest": "back-to-back" if back_to_back else "rested",
    }


def _features_for(store, game_date: str, home: str, away: str, before: str) -> dict | None:
    rows = store.query(
        """
        SELECT values_json FROM features
        WHERE game_date = ? AND home = ? AND away = ? AND as_of <= ?
        ORDER BY as_of DESC LIMIT 1
        """,
        (game_date, home, away, before),
    )
    return json.loads(rows[0]["values_json"]) if rows else None


def settled_bets(store, as_of: datetime, mode: str = "paper") -> list[dict[str, Any]]:
    """Settled bets with a closing price, known by ``as_of``, with their segment context."""
    rows = store.query(
        """
        SELECT * FROM positions
        WHERE mode = ? AND settled_at IS NOT NULL AND settled_at <= ?
          AND closing_price IS NOT NULL AND qty > 0
        ORDER BY settled_at
        """,
        (mode, to_iso(as_of)),
    )
    out = []
    for p in rows:
        price = p["cost"] / p["qty"]
        feats = _features_for(store, p["game_date"], p["home"], p["away"], p["opened_at"]) or {}
        b2b = bool(feats.get("home_b2b" if p["side_team"] == p["home"] else "away_b2b", 0))
        out.append(
            {
                "clv": p["closing_price"] - price,
                "pnl": p["pnl"] or 0.0,
                **context(
                    side_team=p["side_team"],
                    home=p["home"],
                    away=p["away"],
                    game_date=p["game_date"],
                    price=price,
                    back_to_back=b2b,
                ),
            }
        )
    return out


def learn_segments(
    bets: list[dict[str, Any]], cfg: LearningConfig | None = None
) -> list[SegmentStat]:
    cfg = cfg or LearningConfig()
    if not bets:
        return []
    all_clv = [b["clv"] for b in bets]
    sd = statistics.pstdev(all_clv) if len(all_clv) > 1 else 0.0
    sd = max(sd, 0.01)  # a floor so a handful of identical bets can't look certain
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for b in bets:
        for dim in DIMENSIONS:
            groups[(dim, b[dim])].append(b)
    out = []
    for (dim, value), grp in sorted(groups.items()):
        n = len(grp)
        mean = statistics.fmean(g["clv"] for g in grp)
        est = n * mean / (n + cfg.prior_n)
        half = cfg.z * sd / math.sqrt(n + cfg.prior_n)
        lo, hi = est - half, est + half
        if n < cfg.min_n:
            action, mult = "insufficient", 1.0
        elif hi < cfg.avoid_below:
            action, mult = "avoid", math.inf
        elif hi < 0:
            action, mult = "raise_edge", cfg.raise_mult
        elif lo > 0:
            action, mult = "favor", cfg.favor_mult
        else:
            action, mult = "neutral", 1.0
        out.append(
            SegmentStat(dim, value, n, mean, est, lo, hi, sum(g["pnl"] for g in grp), action, mult)
        )
    return out


class SegmentPolicy:
    """Turns learned segment stats into a per-bet minimum edge."""

    def __init__(self, stats: list[SegmentStat], cfg: LearningConfig | None = None) -> None:
        self.cfg = cfg or LearningConfig()
        self.active = {
            (s.dimension, s.value): s for s in stats if s.action in ("avoid", "raise_edge", "favor")
        }

    def threshold(
        self, ctx: dict[str, str], base_min_edge: float
    ) -> tuple[float | None, list[str]]:
        """Minimum edge for a bet in ``ctx``; ``None`` means don't bet."""
        matched = [self.active[(d, ctx[d])] for d in DIMENSIONS if (d, ctx.get(d)) in self.active]
        notes = [
            f"learned {s.action} {s.dimension}={s.value} (CLV {s.estimate:+.3f}, n={s.n})"
            for s in matched
        ]
        if any(s.action == "avoid" for s in matched):
            return None, notes
        raises = [s.multiplier for s in matched if s.action == "raise_edge"]
        favors = [s.multiplier for s in matched if s.action == "favor"]
        mult = max(raises) if raises else (min(favors) if favors else 1.0)
        mult = min(mult, self.cfg.max_mult)
        return max(base_min_edge * mult, self.cfg.min_edge_floor), notes


def save_snapshot(store, stats: list[SegmentStat], now: datetime) -> None:
    with store.conn:
        store.conn.executemany(
            """
            INSERT INTO learned_rules (known_at, kind, dimension, value, n, estimate, lower,
                                       upper, action)
            VALUES (?, 'segment', ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (to_iso(now), s.dimension, s.value, s.n, s.estimate, s.lower, s.upper, s.action)
                for s in stats
            ],
        )
