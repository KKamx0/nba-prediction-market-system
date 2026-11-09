"""Synthetic league generator shared by model tests.

Players have known true offensive/defensive ratings, so tests can check that
the fitting code recovers them. Nothing here touches real data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np

from edgebot.teams import TEAMS
from edgebot.timeutil import box_score_known_at


@dataclass
class League:
    team_games: list[dict]
    player_games: list[dict]
    true_off: dict[int, float]
    true_def: dict[int, float]
    rosters: dict[str, list[int]]
    hca: float
    true_p_home: dict[str, float]


def make_league(
    n_games: int = 900,
    players_per_team: int = 10,
    hca: float = 3.0,
    noise: float = 9.0,
    seed: int = 0,
    start: date = date(2025, 10, 21),
    sd: float = 3.0,
    dnp_rate: float = 0.12,
    game_seed: int | None = None,
    id_prefix: str = "00225",
) -> League:
    """``seed`` fixes the players' true ratings; ``game_seed`` the schedule and noise.

    Same ``seed`` + different ``game_seed`` = the same league playing another season.
    """
    rng = np.random.default_rng(seed)
    teams = sorted(TEAMS)
    rosters, true_off, true_def, base_min = {}, {}, {}, {}
    pid = 1
    for t in teams:
        rosters[t] = []
        mins = np.array([36, 34, 32, 30, 28, 24, 20, 16, 12, 8][:players_per_team], float)
        mins = mins / mins.sum() * 240
        for m in mins:
            rosters[t].append(pid)
            true_off[pid] = rng.normal(0, sd)
            true_def[pid] = rng.normal(0, sd)
            base_min[pid] = m
            pid += 1

    if game_seed is not None:
        rng = np.random.default_rng(game_seed)
    team_games, player_games = [], []
    true_p_home: dict[str, float] = {}
    league_eff = 112.0
    for g in range(n_games):
        day = start + timedelta(days=g // 15)
        home, away = rng.choice(teams, 2, replace=False)
        gid = f"{id_prefix}{g:05d}"
        known = box_score_known_at(day)
        poss = float(rng.normal(99, 3))
        mins = {}
        for t in (home, away):
            # injuries/rest: each player misses a game with probability dnp_rate
            playing = [p for p in rosters[t] if rng.random() > dnp_rate]
            if len(playing) < 8:  # teams always dress enough players
                playing = rosters[t]
            m = np.array([base_min[p] for p in playing]) * rng.uniform(0.7, 1.3, len(playing))
            m = m / m.sum() * 240
            mins[t] = dict(zip(playing, m, strict=True))
        mean_eff = {}
        for off, deff in ((home, away), (away, home)):
            mean_eff[off] = (
                sum(m / 48 * true_off[p] for p, m in mins[off].items())
                - sum(m / 48 * true_def[p] for p, m in mins[deff].items())
                + (hca / 2 if off == home else -hca / 2)
            )
        margin_sd = noise * math.sqrt(2) * poss / 100
        true_p_home[gid] = 0.5 * (
            1
            + math.erf((mean_eff[home] - mean_eff[away]) * poss / 100 / (margin_sd * math.sqrt(2)))
        )
        for off, deff in ((home, away), (away, home)):
            eff = (
                league_eff
                + sum(m / 48 * true_off[p] for p, m in mins[off].items())
                - sum(m / 48 * true_def[p] for p, m in mins[deff].items())
                + (hca / 2 if off == home else -hca / 2)
                + rng.normal(0, noise)
            )
            team_games.append(
                {
                    "game_id": gid,
                    "team": off,
                    "opp": deff,
                    "is_home": int(off == home),
                    "pts": round(eff * poss / 100),
                    "poss": poss,
                    "game_date": day.isoformat(),
                    "known_at": known,
                }
            )
        for t in (home, away):
            for p, m in mins[t].items():
                player_games.append(
                    {
                        "game_id": gid,
                        "player_id": p,
                        "player_name": f"Player {p}",
                        "team": t,
                        "minutes": float(m),
                        "plus_minus": 0.0,
                        "game_date": day.isoformat(),
                        "known_at": known,
                    }
                )
    return League(team_games, player_games, true_off, true_def, rosters, hca, true_p_home)


def as_of(rows: list[dict], cutoff: datetime) -> list[dict]:
    return [r for r in rows if r["known_at"] <= cutoff]


EPOCH = datetime(2025, 10, 1, tzinfo=UTC)


def load_into_store(  # noqa: ANN001
    store,
    league: League,
    season: str,
    *,
    with_odds: bool = True,
    market_noise: float = 0.15,
    odds_seed: int = 0,
) -> None:
    """Write a synthetic league into a Store as if it had been ingested."""
    from datetime import time

    from edgebot.timeutil import box_score_known_at, et_date_to_utc

    by_game: dict[str, list[dict]] = {}
    for r in league.team_games:
        by_game.setdefault(r["game_id"], []).append(r)
    for gid, rows in by_game.items():
        home = next(r for r in rows if r["is_home"])
        away = next(r for r in rows if not r["is_home"])
        day = date.fromisoformat(home["game_date"])
        store.upsert_game(
            game_id=gid,
            season=season,
            game_date=home["game_date"],
            home=home["team"],
            away=away["team"],
            known_at=box_score_known_at(day) - timedelta(days=60),
        )
        if home["pts"] != away["pts"]:
            store.upsert_result(
                game_id=gid, home_pts=home["pts"], away_pts=away["pts"], known_at=home["known_at"]
            )
    store.upsert_team_games(
        {**r, "fga": 85, "fta": 20, "oreb": 10, "tov": 14} for r in league.team_games
    )
    store.upsert_player_games(league.player_games)
    if with_odds:
        rng = np.random.default_rng(odds_seed)
        rows = []
        for gid, rows_g in by_game.items():
            home = next(r for r in rows_g if r["is_home"])
            away = next(r for r in rows_g if not r["is_home"])
            p = league.true_p_home[gid]
            # sportsbook: true probability plus noise, then ~4.5% vig
            lg = math.log(p / (1 - p)) + rng.normal(0, market_noise)
            q = 1 / (1 + math.exp(-lg))
            rows.append(
                {
                    "known_at": et_date_to_utc(date.fromisoformat(home["game_date"]), time(19, 0)),
                    "source": "archive:synthetic",
                    "bookmaker": "consensus",
                    "kind": "closing",
                    "game_date": home["game_date"],
                    "home": home["team"],
                    "away": away["team"],
                    "home_dec": 1 / min(q * 1.045, 0.985),
                    "away_dec": 1 / min((1 - q) * 1.045, 0.985),
                }
            )
        store.insert_odds_quotes(rows)
