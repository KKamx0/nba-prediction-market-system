import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from edgebot.db import MIGRATIONS, Store

T0 = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)


def _game(store, game_id="0022600001", home="ATL", away="LAL"):
    store.upsert_game(
        game_id=game_id,
        season="2026-27",
        game_date="2026-11-09",
        home=home,
        away=away,
        known_at=T0 - timedelta(days=30),
    )


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "edge.db"
    s1 = Store(path)
    assert s1.schema_version() == len(MIGRATIONS)
    s1.close()
    s2 = Store(path)  # reopening must not re-run migrations
    assert s2.schema_version() == len(MIGRATIONS)
    s2.close()


def test_naive_timestamps_rejected(store):
    with pytest.raises(ValueError):
        store.upsert_game(
            game_id="1",
            season="2026-27",
            game_date="2026-11-09",
            home="ATL",
            away="LAL",
            known_at=datetime(2026, 11, 9),  # noqa: DTZ001 - deliberately naive
        )


def test_constraints_reject_bad_rows(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_game(
            game_id="1",
            season="2026-27",
            game_date="2026-11-09",
            home="ATL",
            away="ATL",
            known_at=T0,
        )
    _game(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_injury_rows(
            [
                {
                    "known_at": T0,
                    "team": "ATL",
                    "player_name": "X",
                    "status": "Maybe",
                    "source": "test",
                }
            ]
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_market_snapshot(
            known_at=T0, market_slug="m", bids=[(1.5, 10)], asks=[(0.5, 10)]
        )


def test_player_games_only_visible_once_known(store):
    _game(store)
    known = T0 + timedelta(hours=10)
    store.upsert_player_games(
        [
            {
                "game_id": "0022600001",
                "player_id": 1,
                "player_name": "A",
                "team": "ATL",
                "minutes": 34.5,
                "plus_minus": 7,
                "known_at": known,
            }
        ]
    )
    assert store.player_games_as_of(known - timedelta(seconds=1)) == []
    assert len(store.player_games_as_of(known)) == 1


def test_injury_status_as_of_picks_latest_report_before_cutoff(store):
    rows = [
        {"known_at": T0, "status": "Questionable"},
        {"known_at": T0 + timedelta(hours=2), "status": "Out"},
        {"known_at": T0 + timedelta(hours=4), "status": "Available"},
    ]
    store.insert_injury_rows(
        {**r, "team": "ATL", "player_name": "Young, Trae", "source": "test"} for r in rows
    )
    status = lambda t: [r["status"] for r in store.injury_status_as_of("ATL", t)]  # noqa: E731
    assert status(T0 - timedelta(minutes=1)) == []
    assert status(T0 + timedelta(hours=1)) == ["Questionable"]
    assert status(T0 + timedelta(hours=3)) == ["Out"]
    assert status(T0 + timedelta(hours=5)) == ["Available"]


def test_injury_rows_deduplicate(store):
    row = {
        "known_at": T0,
        "team": "ATL",
        "player_name": "A",
        "status": "Out",
        "source": "test",
    }
    assert store.insert_injury_rows([row]) == 1
    assert store.insert_injury_rows([row]) == 0


def test_latest_market_snapshot_as_of(store):
    for minutes, bid in [(0, 0.40), (10, 0.45), (20, 0.50)]:
        store.insert_market_snapshot(
            known_at=T0 + timedelta(minutes=minutes),
            market_slug="aec-nba-lal-atl-2026-11-09",
            bids=[(bid, 100.0)],
            asks=[(bid + 0.01, 100.0)],
        )
    snap = store.latest_market_snapshot_as_of(
        "aec-nba-lal-atl-2026-11-09", T0 + timedelta(minutes=15)
    )
    assert snap["best_bid"] == pytest.approx(0.45)
    assert (
        store.latest_market_snapshot_as_of("aec-nba-lal-atl-2026-11-09", T0 - timedelta(1)) is None
    )


def test_odds_as_of_latest_per_book(store):
    base = {
        "source": "odds_api",
        "kind": "live",
        "game_date": "2026-11-09",
        "home": "ATL",
        "away": "LAL",
    }
    store.insert_odds_quotes(
        [
            {**base, "bookmaker": "pinnacle", "known_at": T0, "home_dec": 2.0, "away_dec": 1.9},
            {
                **base,
                "bookmaker": "pinnacle",
                "known_at": T0 + timedelta(hours=1),
                "home_dec": 2.1,
                "away_dec": 1.8,
            },
            {**base, "bookmaker": "draftkings", "known_at": T0, "home_dec": 1.95, "away_dec": 1.95},
        ]
    )
    rows = store.odds_as_of(
        game_date="2026-11-09", home="ATL", away="LAL", as_of=T0 + timedelta(minutes=30)
    )
    assert {(r["bookmaker"], r["home_dec"]) for r in rows} == {
        ("pinnacle", 2.0),
        ("draftkings", 1.95),
    }
    rows = store.odds_as_of(
        game_date="2026-11-09", home="ATL", away="LAL", as_of=T0 + timedelta(hours=2)
    )
    assert {(r["bookmaker"], r["home_dec"]) for r in rows} == {
        ("pinnacle", 2.1),
        ("draftkings", 1.95),
    }


def test_invalid_odds_raise_instead_of_being_silently_dropped(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_odds_quotes(
            [
                {
                    "known_at": T0,
                    "source": "x",
                    "kind": "live",
                    "game_date": "2026-11-09",
                    "home": "ATL",
                    "away": "LAL",
                    "home_dec": 0.5,  # decimal odds must be > 1
                    "away_dec": 1.9,
                }
            ]
        )
