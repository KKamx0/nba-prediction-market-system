from datetime import UTC, date, datetime

import pytest

from edgebot.model.strength import RatingsBook, rosters_as_of, statuses_for, team_tonight
from tests.synthetic import load_into_store, make_league

SEASON_START = date(2025, 10, 21)


@pytest.fixture(scope="module")
def loaded():
    from edgebot.db import Store

    store = Store(":memory:")
    league = make_league(n_games=1230, seed=3, start=SEASON_START)
    load_into_store(store, league, "2025-26")
    yield store, league
    store.close()


def test_ratings_as_of_only_see_known_games(loaded):
    store, _ = loaded
    book = RatingsBook(store)
    before_any = book.as_of(datetime(2025, 10, 21, 23, tzinfo=UTC), SEASON_START)
    assert before_any.n_games == 0
    mid = book.as_of(datetime(2026, 1, 1, tzinfo=UTC), date(2026, 1, 1))
    full = book.as_of(datetime(2026, 7, 1, tzinfo=UTC), date(2026, 6, 30))
    assert 0 < mid.n_games < full.n_games == 1230


def test_next_season_starts_from_shrunk_season_end_prior(loaded):
    store, _ = loaded
    book = RatingsBook(store)
    end = book.season_end("2025-26")
    opening_night = book.as_of(datetime(2026, 10, 20, tzinfo=UTC), date(2026, 10, 20))
    assert opening_night.n_games == 0
    pid = next(iter(end.off))
    assert opening_night.o(pid) == pytest.approx(end.config.carryover * end.o(pid))


def test_star_out_lowers_tonight_strength(loaded):
    store, league = loaded
    game_day = date(2026, 2, 1)
    as_of = datetime(2026, 2, 1, 20, tzinfo=UTC)
    ratings = RatingsBook(store).as_of(as_of, game_day)
    team = "BOS"
    roster = rosters_as_of(store, game_day, as_of)[team]
    star = max(roster, key=lambda p: ratings.net(p) * roster[p][1].avg10)
    star_name = roster[star][0]

    healthy = team_tonight(team, ratings, roster, {})
    out = team_tonight(team, ratings, roster, {star_name: "Out"})
    gtd = team_tonight(team, ratings, roster, {star_name: "Questionable"})
    assert out.expected < healthy.expected
    assert out.full_strength == pytest.approx(healthy.full_strength)
    assert gtd.all_sit < gtd.expected < gtd.all_play


def test_statuses_for_tonight_ignore_other_dates(store):
    t = datetime(2026, 2, 1, 18, tzinfo=UTC)
    store.insert_injury_rows(
        [
            {
                "known_at": t,
                "game_date": "2026-02-01",
                "team": "BOS",
                "player_name": "A",
                "status": "Out",
                "source": "x",
            },
            {
                "known_at": t,
                "game_date": "2026-01-30",
                "team": "BOS",
                "player_name": "B",
                "status": "Out",
                "source": "x",
            },
        ]
    )
    assert statuses_for(store, "BOS", date(2026, 2, 1), t) == {"A": "Out"}
