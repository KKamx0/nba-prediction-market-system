"""Team strength tonight, computed strictly from data known at decision time.

``RatingsBook`` fits player ratings as of any instant. The prior for season S
is the end-of-season fit of S-1 (scaled by ``carryover``), cached so each
season is fit once.

``team_tonight`` turns ratings + projected minutes into net ratings (points
per 100 possessions) for each availability scenario.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from edgebot.model.minutes import MinutesModel, TeamMinutes, build_rosters, project_team
from edgebot.model.ratings import PlayerRatings, RatingConfig, fit_ratings, team_strength
from edgebot.timeutil import season_for_date, to_iso


def _prev_season(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


class RatingsBook:
    def __init__(self, store, config: RatingConfig | None = None) -> None:
        self.store = store
        self.config = config or RatingConfig()
        self._season_end: dict[str, PlayerRatings | None] = {}
        seasons = [r["season"] for r in store.query("SELECT DISTINCT season FROM games")]
        self._first_season = min(seasons) if seasons else None

    _FAR_FUTURE = "9999-12-31T00:00:00.000000+00:00"

    def _rows(self, season: str, as_of: datetime | None):
        cutoff = to_iso(as_of) if as_of is not None else self._FAR_FUTURE
        tg = self.store.query(
            """
            SELECT tg.* FROM team_games tg JOIN games g USING (game_id)
            WHERE g.season = ? AND tg.known_at <= ?
            """,
            (season, cutoff),
        )
        pg = self.store.query(
            """
            SELECT pg.* FROM player_games pg JOIN games g USING (game_id)
            WHERE g.season = ? AND pg.known_at <= ?
            """,
            (season, cutoff),
        )
        return tg, pg

    def season_end(self, season: str) -> PlayerRatings | None:
        """Ratings after every game of ``season`` (used only as next season's prior)."""
        if season in self._season_end:
            return self._season_end[season]
        if self._first_season is None or season < self._first_season:
            self._season_end[season] = None
            return None
        prior_ratings = self.season_end(_prev_season(season))
        tg, pg = self._rows(season, None)
        result = (
            fit_ratings(tg, pg, prior_ratings.as_prior() if prior_ratings else None, self.config)
            if tg
            else None
        )
        self._season_end[season] = result
        return result

    def as_of(self, as_of: datetime, game_day: date) -> PlayerRatings:
        season = season_for_date(game_day)
        prior_ratings = self.season_end(_prev_season(season))
        tg, pg = self._rows(season, as_of)
        return fit_ratings(tg, pg, prior_ratings.as_prior() if prior_ratings else None, self.config)


@dataclass
class TeamTonight:
    team: str
    expected: float  # net rating per 100 poss, probability-weighted availability
    all_play: float  # every uncertain player plays
    all_sit: float  # every uncertain player sits
    full_strength: float  # nobody injured
    minutes: TeamMinutes


def statuses_for(store, team: str, game_day: date, as_of: datetime) -> dict[str, str]:
    """Latest injury status per player, from reports about tonight's game."""
    return {
        r["player_name"]: r["status"]
        for r in store.injury_status_as_of(team, as_of)
        if r["game_date"] in (None, game_day.isoformat())
    }


def team_tonight(
    team: str,
    ratings: PlayerRatings,
    roster,
    statuses: Mapping[str, str],
    *,
    back_to_back: bool = False,
    minutes_model: MinutesModel | None = None,
    minutes_delta: Mapping[str, float] | None = None,
) -> TeamTonight:
    tm = project_team(
        team,
        roster,
        statuses,
        model=minutes_model,
        back_to_back=back_to_back,
        minutes_delta=minutes_delta,
    )
    return TeamTonight(
        team=team,
        expected=team_strength(ratings, tm.expected),
        all_play=team_strength(ratings, tm.all_play),
        all_sit=team_strength(ratings, tm.all_sit),
        full_strength=team_strength(ratings, tm.full_strength),
        minutes=tm,
    )


def rosters_as_of(store, game_day: date, as_of: datetime):
    return build_rosters(store.player_lines_as_of(as_of), game_day)
