"""NBA box scores and schedule via nba_api, cached locally and stored as-of.

Availability rules (see ``timeutil.box_score_known_at``):

* A game's final score and every player's minutes and plus-minus become
  known at 08:00 UTC the day after the game, safely after any final.
* A scheduled game is known from the earlier of when we fetched it and 24h
  before tip; NBA schedules are published months ahead.

stats.nba.com blocks many datacenter IPs, so run this from a home connection.
Raw responses are cached under ``cache/nba_api`` (gitignored). Completed
seasons are cached permanently; the current season is re-fetched on request.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from edgebot.teams import resolve
from edgebot.timeutil import box_score_known_at, parse_iso, utc_now

log = logging.getLogger(__name__)

Fetcher = Callable[[str, dict[str, Any]], pd.DataFrame]

# GAME_ID prefixes: 002 regular season, 004 playoffs, 005 play-in. Preseason
# (001) and All-Star (003) games are excluded.
COMPETITIVE_PREFIXES = ("002", "004", "005")


def nba_api_fetcher(endpoint: str, params: dict[str, Any], pause_s: float = 1.5) -> pd.DataFrame:
    """Call nba_api politely: one request at a time with a pause in between."""
    from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2

    time.sleep(pause_s)
    if endpoint == "leaguegamelog":
        return leaguegamelog.LeagueGameLog(timeout=60, **params).get_data_frames()[0]
    if endpoint == "schedule":
        return scheduleleaguev2.ScheduleLeagueV2(timeout=60, **params).get_data_frames()[0]
    raise ValueError(f"unknown endpoint: {endpoint}")


def parse_minutes(value: Any) -> float:
    """nba_api reports minutes as a number or as 'MM:SS'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, str) and ":" in value:
        mm, ss = value.split(":", 1)
        return int(mm) + int(ss) / 60
    return float(value)


def _read_cache(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype={"GAME_ID": str, "gameId": str})
    except pd.errors.EmptyDataError:  # e.g. playoffs not played yet
        return pd.DataFrame()


class NbaStats:
    def __init__(self, cache_dir: str | Path = "cache/nba_api", fetcher: Fetcher | None = None):
        self.cache_dir = Path(cache_dir)
        self.fetcher = fetcher or nba_api_fetcher

    def _cached(self, endpoint: str, params: dict[str, Any], refresh: bool) -> pd.DataFrame:
        key = "_".join([endpoint, *(f"{k}-{v}" for k, v in sorted(params.items()))])
        path = self.cache_dir / f"{key.replace(' ', '')}.csv"
        if path.exists() and not refresh:
            return _read_cache(path)
        df = self.fetcher(endpoint, params)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return _read_cache(path)

    def game_logs(self, season: str, kind: str, season_type: str, refresh: bool) -> pd.DataFrame:
        if kind not in ("P", "T"):
            raise ValueError("kind must be 'P' (players) or 'T' (teams)")
        return self._cached(
            "leaguegamelog",
            {
                "season": season,
                "player_or_team_abbreviation": kind,
                "season_type_all_star": season_type,
            },
            refresh,
        )

    def schedule(self, season: str, refresh: bool) -> pd.DataFrame:
        return self._cached("schedule", {"season": season}, refresh)


def _competitive(game_id: str) -> bool:
    return str(game_id).zfill(10)[:3] in COMPETITIVE_PREFIXES


def games_from_team_logs(team_logs: pd.DataFrame, season: str) -> list[dict[str, Any]]:
    """Pair the two team rows of each game into one home/away record."""
    out = []
    for game_id, grp in team_logs.groupby("GAME_ID"):
        game_id = str(game_id).zfill(10)
        if not _competitive(game_id):
            continue
        if len(grp) != 2:
            log.warning("game %s has %d team rows; skipped", game_id, len(grp))
            continue
        home_rows = grp[grp["MATCHUP"].str.contains(" vs. ", regex=False)]
        away_rows = grp[grp["MATCHUP"].str.contains(" @ ", regex=False)]
        if len(home_rows) != 1 or len(away_rows) != 1:
            log.warning("game %s has no clear home/away; skipped", game_id)
            continue
        h, a = home_rows.iloc[0], away_rows.iloc[0]
        game_day = date.fromisoformat(str(h["GAME_DATE"])[:10])
        out.append(
            {
                "game_id": game_id,
                "season": season,
                "game_date": game_day.isoformat(),
                "home": resolve(h["TEAM_ABBREVIATION"]),
                "away": resolve(a["TEAM_ABBREVIATION"]),
                "home_pts": int(h["PTS"]),
                "away_pts": int(a["PTS"]),
                "known_at": box_score_known_at(game_day),
            }
        )
    return out


def possessions(fga: float, fta: float, oreb: float, tov: float) -> float:
    """Standard single-team possession estimate."""
    return fga + 0.44 * fta - oreb + tov


def team_rows(team_logs: pd.DataFrame, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-team box totals for games already paired by ``games_from_team_logs``."""
    by_id = {g["game_id"]: g for g in games}
    out = []
    for rec in team_logs.to_dict("records"):
        game_id = str(rec["GAME_ID"]).zfill(10)
        g = by_id.get(game_id)
        if g is None:
            continue
        team = resolve(rec["TEAM_ABBREVIATION"])
        out.append(
            {
                "game_id": game_id,
                "team": team,
                "opp": g["away"] if team == g["home"] else g["home"],
                "is_home": team == g["home"],
                "pts": int(rec["PTS"]),
                "fga": int(rec["FGA"]),
                "fta": int(rec["FTA"]),
                "oreb": int(rec["OREB"]),
                "tov": int(rec["TOV"]),
                "poss": possessions(rec["FGA"], rec["FTA"], rec["OREB"], rec["TOV"]),
                "known_at": g["known_at"],
            }
        )
    return out


def player_rows(player_logs: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for rec in player_logs.to_dict("records"):
        game_id = str(rec["GAME_ID"]).zfill(10)
        if not _competitive(game_id):
            continue
        game_day = date.fromisoformat(str(rec["GAME_DATE"])[:10])
        pm = rec.get("PLUS_MINUS")
        out.append(
            {
                "game_id": game_id,
                "player_id": int(rec["PLAYER_ID"]),
                "player_name": str(rec["PLAYER_NAME"]),
                "team": resolve(rec["TEAM_ABBREVIATION"]),
                "minutes": parse_minutes(rec.get("MIN")),
                "plus_minus": None if pm is None or pd.isna(pm) else float(pm),
                "pts": None if pd.isna(rec.get("PTS")) else int(rec["PTS"]),
                "started": None,
                "known_at": box_score_known_at(game_day),
            }
        )
    return out


def ingest_season(
    store,  # noqa: ANN001 - edgebot.db.Store
    stats: NbaStats,
    season: str,
    *,
    season_types: tuple[str, ...] = ("Regular Season", "PlayIn", "Playoffs"),
    refresh: bool = False,
) -> dict[str, int]:
    """Load one season's results and player lines into the store."""
    counts = {"games": 0, "player_games": 0}
    for st in season_types:
        teams = stats.game_logs(season, "T", st, refresh)
        if teams.empty:
            continue
        games = games_from_team_logs(teams, season)
        for g in games:
            store.upsert_game(
                game_id=g["game_id"],
                season=season,
                game_date=g["game_date"],
                home=g["home"],
                away=g["away"],
                known_at=g["known_at"] - timedelta(days=60),
            )
            store.upsert_result(
                game_id=g["game_id"],
                home_pts=g["home_pts"],
                away_pts=g["away_pts"],
                known_at=g["known_at"],
            )
        store.upsert_team_games(team_rows(teams, games))
        known_ids = {g["game_id"] for g in games}
        players = [
            r
            for r in player_rows(stats.game_logs(season, "P", st, refresh))
            if r["game_id"] in known_ids
        ]
        store.upsert_player_games(players)
        counts["games"] += len(games)
        counts["player_games"] += len(players)
    return counts


def schedule_rows(schedule: pd.DataFrame, season: str, now: datetime) -> list[dict[str, Any]]:
    out = []
    for rec in schedule.to_dict("records"):
        game_id = str(rec["gameId"]).zfill(10)
        if not _competitive(game_id):
            continue
        try:
            tip = parse_iso(str(rec["gameDateTimeUTC"]))
            home = resolve(str(rec["homeTeam_teamTricode"]))
            away = resolve(str(rec["awayTeam_teamTricode"]))
        except ValueError:
            continue  # TBD playoff slots have no teams or time yet
        out.append(
            {
                "game_id": game_id,
                "season": season,
                "game_date": str(rec["gameDateEst"])[:10],
                "home": home,
                "away": away,
                "tip_utc": tip,
                "known_at": min(now, tip - timedelta(days=1)),
            }
        )
    return out


def ingest_schedule(
    store, stats: NbaStats, season: str, *, refresh: bool = True
) -> int:  # noqa: ANN001
    rows = schedule_rows(stats.schedule(season, refresh), season, utc_now())
    for r in rows:
        store.upsert_game(**r)
    return len(rows)
