"""Per-player minutes corrections, learned from our own projection errors.

At every decision the bot logs the minutes it projected for each player. Once
the box score is known, ``actual - projected`` is that projection's error
(a player who was projected to play but didn't appear counts as 0 minutes).
Some players are systematically off: rested more than their averages suggest,
or used more by a new coach. Their average error, shrunk toward zero, becomes
an additive correction in future projections:

    bias = sum(errors) / (n + prior_n), clipped to +/- cap

Only errors whose game results are known at ``as_of`` are used, so the
correction is as-of safe like every other input.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from edgebot.timeutil import to_iso


@dataclass(frozen=True)
class MinutesBiasConfig:
    prior_n: float = 8.0
    min_n: int = 3
    cap: float = 6.0


def log_projections(store, game_date: str, team: str, minutes, known_at: datetime) -> int:
    """``minutes`` is a ``TeamMinutes``; its ``expected`` scenario is what we bet on."""
    rows = [
        (
            to_iso(known_at),
            game_date,
            team,
            pid,
            minutes.names.get(pid, str(pid)),
            round(min(max(m, 0.0), 48.0), 3),
            minutes.statuses.get(pid),
        )
        for pid, m in minutes.expected.items()
    ]
    with store.conn:
        store.conn.executemany(
            """
            INSERT INTO minutes_projections (known_at, game_date, team, player_id, player_name,
                                             projected, status)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
            """,
            rows,
        )
    return len(rows)


def projection_errors(store, as_of: datetime) -> list[dict]:
    """Last projection per player-game vs actual minutes, for games final by ``as_of``."""
    cutoff = to_iso(as_of)
    return [
        dict(r)
        for r in store.query(
            """
            WITH last AS (
                SELECT game_date, team, player_id, MAX(known_at) AS t
                FROM minutes_projections WHERE known_at <= ?
                GROUP BY game_date, team, player_id
            )
            SELECT mp.game_date, mp.team, mp.player_id, mp.player_name, mp.projected,
                   COALESCE(pg.minutes, 0.0) AS actual
            FROM last
            JOIN minutes_projections mp
              ON mp.game_date = last.game_date AND mp.team = last.team
             AND mp.player_id = last.player_id AND mp.known_at = last.t
            JOIN games g ON g.game_date = mp.game_date AND (g.home = mp.team OR g.away = mp.team)
            JOIN game_results r ON r.game_id = g.game_id AND r.known_at <= ?
            LEFT JOIN player_games pg
              ON pg.game_id = g.game_id AND pg.player_id = mp.player_id AND pg.known_at <= ?
            """,
            (cutoff, cutoff, cutoff),
        )
    ]


def learn_bias(errors: list[dict], cfg: MinutesBiasConfig | None = None) -> dict[str, dict]:
    cfg = cfg or MinutesBiasConfig()
    by_player: dict[int, list[dict]] = defaultdict(list)
    for e in errors:
        by_player[e["player_id"]].append(e)
    out = {}
    for rows in by_player.values():
        n = len(rows)
        if n < cfg.min_n:
            continue
        total = sum(r["actual"] - r["projected"] for r in rows)
        bias = max(-cfg.cap, min(cfg.cap, total / (n + cfg.prior_n)))
        out[rows[-1]["player_name"]] = {"bias": bias, "n": n, "mean_error": total / n}
    return out


def minutes_bias_as_of(
    store, as_of: datetime, cfg: MinutesBiasConfig | None = None
) -> dict[str, float]:
    """Player name -> minutes correction, using only errors known at ``as_of``."""
    return {name: v["bias"] for name, v in learn_bias(projection_errors(store, as_of), cfg).items()}
