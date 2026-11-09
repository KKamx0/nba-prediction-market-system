"""Game features, computed strictly from data known at the decision time.

Every input goes through an ``*_as_of`` read, so a feature vector for a game
can only depend on rows with ``known_at <= as_of``. ``tests/test_leakage.py``
checks this end to end by mutating everything after ``as_of`` and asserting
the vector doesn't move.

Perspective is always the home team.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from edgebot.devig import consensus_home_prob
from edgebot.model.minutes import MinutesModel
from edgebot.model.strength import RatingsBook, rosters_as_of, statuses_for, team_tonight
from edgebot.news import news_adjustments_as_of
from edgebot.teams import TEAMS, travel_km
from edgebot.timeutil import utc_now

FEATURE_VERSION = "v1"

# Inputs the game models train on. The scenario fields below are carried for
# the trading layer's plays/sits check but are not model inputs.
MODEL_FEATURES = [
    "market_logit",
    "strength_diff",
    "full_strength_diff",
    "injury_drag_diff",
    "pace",
    "rest_diff",
    "home_b2b",
    "away_b2b",
    "travel_home_kkm",
    "travel_away_kkm",
    "tz_shift_away",
    "density_diff",
    "consistency_home",
    "consistency_away",
]


@dataclass
class GameFeatures:
    game_date: str
    home: str
    away: str
    as_of: datetime
    market_prob: float | None
    market_logit: float | None
    strength_diff: float
    strength_diff_play: float
    strength_diff_sit: float
    full_strength_diff: float
    injury_drag_diff: float
    pace: float
    rest_diff: float
    home_b2b: int
    away_b2b: int
    travel_home_kkm: float
    travel_away_kkm: float
    tz_shift_away: float
    density_diff: float
    consistency_home: float
    consistency_away: float
    has_uncertainty: int

    def model_vector(self) -> dict[str, float | None]:
        return {k: getattr(self, k) for k in MODEL_FEATURES}

    def to_record(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d


@dataclass
class _TeamGame:
    day: date
    location: str  # home team of that game = whose arena
    poss: float
    net100: float


def _tz_offset_hours(abbr: str, when: datetime) -> float:
    off = when.astimezone(ZoneInfo(TEAMS[abbr].tz)).utcoffset()
    return off.total_seconds() / 3600 if off else 0.0


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


class FeatureBuilder:
    """Builds features for many games, caching the expensive per-day fits."""

    def __init__(self, store, minutes_model: MinutesModel | None = None) -> None:
        self.store = store
        self.book = RatingsBook(store)
        self.minutes_model = minutes_model or MinutesModel()
        self._cache_key: tuple[date, datetime] | None = None
        self._ctx: tuple | None = None

    def _context(self, game_day: date, as_of: datetime):
        key = (game_day, as_of)
        if self._cache_key != key:
            ratings = self.book.as_of(as_of, game_day)
            rosters = rosters_as_of(self.store, game_day, as_of)
            logs: dict[str, list[_TeamGame]] = defaultdict(list)
            rows = self.store.team_games_as_of(as_of)
            by_game: dict[str, dict[str, object]] = defaultdict(dict)
            for r in rows:
                by_game[r["game_id"]][r["team"]] = r
            for g in by_game.values():
                if len(g) != 2:
                    continue
                (t1, r1), (t2, r2) = g.items()
                poss = (r1["poss"] + r2["poss"]) / 2
                for t, me, opp in ((t1, r1, r2), (t2, r2, r1)):
                    logs[t].append(
                        _TeamGame(
                            day=date.fromisoformat(me["game_date"]),
                            location=t if me["is_home"] else me["opp"],
                            poss=poss,
                            net100=100 * (me["pts"] - opp["pts"]) / poss,
                        )
                    )
            for t in logs:
                logs[t] = sorted((g for g in logs[t] if g.day < game_day), key=lambda g: g.day)
            self._cache_key, self._ctx = key, (ratings, rosters, logs)
        return self._ctx

    def build(
        self,
        *,
        game_date: str,
        home: str,
        away: str,
        as_of: datetime,
        minutes_delta: dict[str, float] | None = None,
        save: bool = True,
    ) -> GameFeatures:
        game_day = date.fromisoformat(game_date)
        ratings, rosters, logs = self._context(game_day, as_of)

        def recent(team: str) -> list[_TeamGame]:
            return [g for g in logs.get(team, []) if g.day < game_day]

        def rest(team: str) -> float:
            past = recent(team)
            return min((game_day - past[-1].day).days, 5) if past else 5.0

        def travel(team: str) -> float:
            past = recent(team)
            return travel_km(past[-1].location, home) / 1000 if past else 0.0

        def density(team: str) -> int:
            return sum(1 for g in recent(team) if 0 < (game_day - g.day).days <= 7)

        def consistency(team: str) -> float:
            last = [g.net100 for g in recent(team)[-10:]]
            return statistics.pstdev(last) if len(last) > 2 else 12.0

        def pace(team: str) -> float:
            last = [g.poss for g in recent(team)[-10:]]
            return statistics.fmean(last) if last else 99.0

        sides = {}
        for team in (home, away):
            official = statuses_for(self.store, team, game_day, as_of)
            # LLM news extractions (as of the decision time) only adjust minutes and fill
            # in players the official report doesn't list; they never touch probabilities.
            news = news_adjustments_as_of(self.store, team, as_of, official)
            sides[team] = team_tonight(
                team,
                ratings,
                rosters.get(team, {}),
                {**news.statuses, **official},
                back_to_back=rest(team) <= 1,
                minutes_model=self.minutes_model,
                minutes_delta={**news.minutes_delta, **(minutes_delta or {})},
            )
        h, a = sides[home], sides[away]

        quotes = self.store.odds_as_of(game_date=game_date, home=home, away=away, as_of=as_of)
        market = consensus_home_prob((q["home_dec"], q["away_dec"]) for q in quotes)

        prev_away = recent(away)
        tz_shift = (
            abs(_tz_offset_hours(home, as_of) - _tz_offset_hours(prev_away[-1].location, as_of))
            if prev_away
            else 0.0
        )
        feats = GameFeatures(
            game_date=game_date,
            home=home,
            away=away,
            as_of=as_of,
            market_prob=market,
            market_logit=None if market is None else logit(market),
            strength_diff=h.expected - a.expected,
            strength_diff_play=h.all_play - a.all_play,
            strength_diff_sit=h.all_sit - a.all_sit,
            full_strength_diff=h.full_strength - a.full_strength,
            injury_drag_diff=(h.full_strength - h.expected) - (a.full_strength - a.expected),
            pace=(pace(home) + pace(away)) / 2,
            rest_diff=rest(home) - rest(away),
            home_b2b=int(rest(home) <= 1),
            away_b2b=int(rest(away) <= 1),
            travel_home_kkm=travel(home),
            travel_away_kkm=travel(away),
            tz_shift_away=tz_shift,
            density_diff=density(home) - density(away),
            consistency_home=consistency(home),
            consistency_away=consistency(away),
            has_uncertainty=int(h.minutes.has_uncertainty or a.minutes.has_uncertainty),
        )
        if save:
            self.store.save_features(
                game_date=game_date,
                home=home,
                away=away,
                as_of=as_of,
                version=FEATURE_VERSION,
                values=feats.to_record(),
                known_at=utc_now(),
            )
        return feats
