"""Regularized plus-minus player ratings from game-level box data.

Each team-game contributes one *offensive* observation:

    100 * pts / poss - league_eff
        = sum_i w_i * O_i  -  sum_j w_j * D_j  +/- hca / 2  + noise

where ``i`` runs over the offense's players, ``j`` over the defense's,
``w = 5 * minutes / team_minutes`` (fraction of the game on the floor, so a
team's weights sum to 5), and ``D > 0`` means good defense. Observations are
weighted by possessions.

The ridge penalty pulls each player toward a prior instead of toward zero:

    minimize  sum_g poss_g * (y_g - x_g . b)^2  +  lam * sum_k (b_k - prior_k)^2

The prior is last season's rating times ``carryover`` (regression to the
mean). A player with a few minutes stays near the prior; as current-season
minutes accumulate, the data term dominates and the prior phases out. Unseen
players start at ``rookie_prior``.

This is a game-level approximation of RAPM: true RAPM uses stint-level
lineup data. On synthetic leagues with NBA-like lineup churn, one season of
games recovers individual player value with correlation ~0.5 and two
seasons ~0.65. Team-level strength (what the game model actually uses) is
recovered with correlation > 0.85. Individual ratings are noisy; the prior and
the team aggregation are what make them usable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class RatingConfig:
    prior_weight: float = 2000.0  # lam; best on synthetic leagues (see tests/test_ratings.py)
    carryover: float = 0.75  # share of last season's rating kept as this season's prior
    rookie_prior: tuple[float, float] = (-1.0, -0.5)  # (O, D) per 100 poss for unseen players
    min_games_for_hca: int = 50


@dataclass
class PlayerRatings:
    off: dict[int, float] = field(default_factory=dict)
    deff: dict[int, float] = field(default_factory=dict)
    minutes: dict[int, float] = field(default_factory=dict)  # current-sample minutes
    league_eff: float = 112.0  # points per 100 possessions
    hca: float = 2.5  # home-court advantage, points per 100 possessions
    n_games: int = 0
    config: RatingConfig = field(default_factory=RatingConfig)

    def o(self, pid: int) -> float:
        return self.off.get(pid, self.config.rookie_prior[0])

    def d(self, pid: int) -> float:
        return self.deff.get(pid, self.config.rookie_prior[1])

    def net(self, pid: int) -> float:
        return self.o(pid) + self.d(pid)

    def as_prior(self) -> dict[int, tuple[float, float]]:
        c = self.config.carryover
        return {pid: (c * self.off[pid], c * self.deff[pid]) for pid in self.off}


def fit_ratings(
    team_games: Iterable[Mapping[str, Any]],
    player_games: Iterable[Mapping[str, Any]],
    prior: Mapping[int, tuple[float, float]] | None = None,
    config: RatingConfig | None = None,
) -> PlayerRatings:
    """Fit O/D ratings on the given (already as-of filtered) rows."""
    cfg = config or RatingConfig()
    prior = dict(prior or {})

    tg = [dict(r) for r in team_games]
    by_game: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in tg:
        by_game[r["game_id"]][r["team"]] = r
    lines: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    for r in player_games:
        if r["minutes"] > 0:
            lines[(r["game_id"], r["team"])].append((int(r["player_id"]), float(r["minutes"])))

    games = [g for gid, g in by_game.items() if len(g) == 2 and all(lines.get((gid, t)) for t in g)]
    if not games:
        ratings = PlayerRatings(config=cfg)
        for pid, (o, d) in prior.items():
            ratings.off[pid], ratings.deff[pid] = o, d
        return ratings

    total_pts = sum(r["pts"] for g in games for r in g.values())
    total_poss = sum(r["poss"] for g in games for r in g.values())
    league_eff = 100 * total_pts / total_poss

    players = sorted({pid for key in lines for pid, _ in lines[key]} | set(prior))
    col = {pid: k for k, pid in enumerate(players)}
    n_p = len(players)
    n_cols = 2 * n_p + 1  # O block, D block, HCA
    hca_col = 2 * n_p

    rows_x, ys, ws = [], [], []
    minutes: dict[int, float] = defaultdict(float)
    for g in games:
        (t1, r1), (t2, r2) = g.items()
        poss = (r1["poss"] + r2["poss"]) / 2
        for off_team, off_row, def_team in ((t1, r1, t2), (t2, r2, t1)):
            x = np.zeros(n_cols)
            for team, block, sign in ((off_team, 0, 1.0), (def_team, n_p, -1.0)):
                plist = lines[(off_row["game_id"], team)]
                team_min = sum(m for _, m in plist)
                for pid, m in plist:
                    x[block + col[pid]] += sign * 5 * m / team_min
            x[hca_col] = 0.5 if off_row["is_home"] else -0.5
            rows_x.append(x)
            ys.append(100 * off_row["pts"] / poss - league_eff)
            ws.append(poss)
    used = {r["game_id"] for g in games for r in g.values()}
    for (gid, _team), plist in lines.items():
        if gid in used:
            for pid, m in plist:
                minutes[pid] += m

    X, y, w = np.array(rows_x), np.array(ys), np.array(ws)
    prior_vec = np.zeros(n_cols)
    for pid, k in col.items():
        o, d = prior.get(pid, cfg.rookie_prior)
        prior_vec[k], prior_vec[n_p + k] = o, d
    lam = np.full(n_cols, cfg.prior_weight)
    if len(games) >= cfg.min_games_for_hca:
        lam[hca_col] = 1e-6  # home court is a free parameter once there is data
    else:
        prior_vec[hca_col] = PlayerRatings.hca
        lam[hca_col] = cfg.prior_weight

    xtw = X.T * w
    beta = np.linalg.solve(xtw @ X + np.diag(lam), xtw @ y + lam * prior_vec)

    ratings = PlayerRatings(
        league_eff=league_eff, hca=float(beta[hca_col]), n_games=len(games), config=cfg
    )
    for pid, k in col.items():
        ratings.off[pid] = float(beta[k])
        ratings.deff[pid] = float(beta[n_p + k])
        ratings.minutes[pid] = minutes.get(pid, 0.0)
    return ratings


def team_strength(ratings: PlayerRatings, projected_minutes: Mapping[int, float]) -> float:
    """Net rating per 100 possessions for a lineup distribution (minutes sum ~240)."""
    total = sum(projected_minutes.values())
    if total <= 0:
        return 0.0
    return sum(5 * m / total * ratings.net(pid) for pid, m in projected_minutes.items())
