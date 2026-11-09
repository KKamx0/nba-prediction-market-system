from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from edgebot.db import Store
from edgebot.ingest.nba_stats import (
    NbaStats,
    games_from_team_logs,
    ingest_schedule,
    ingest_season,
    parse_minutes,
    player_rows,
    schedule_rows,
)

TEAM_LOGS = pd.DataFrame(
    {
        "GAME_ID": ["0022600001", "0022600001", "0012600005", "0012600005"],
        "GAME_DATE": ["2026-10-21", "2026-10-21", "2026-10-05", "2026-10-05"],
        "TEAM_ABBREVIATION": ["LAL", "GSW", "BOS", "NYK"],
        "MATCHUP": ["LAL vs. GSW", "GSW @ LAL", "BOS vs. NYK", "NYK @ BOS"],
        "PTS": [112, 108, 100, 90],
    }
)
PLAYER_LOGS = pd.DataFrame(
    {
        "GAME_ID": ["0022600001", "0022600001", "0012600005"],
        "GAME_DATE": ["2026-10-21", "2026-10-21", "2026-10-05"],
        "PLAYER_ID": [2544, 201939, 1],
        "PLAYER_NAME": ["LeBron James", "Stephen Curry", "Preseason Guy"],
        "TEAM_ABBREVIATION": ["LAL", "GSW", "BOS"],
        "MIN": [35.0, "33:30", 20],
        "PTS": [28, 31, 5],
        "PLUS_MINUS": [6.0, -4.0, 1.0],
    }
)


@pytest.mark.parametrize(
    "value,expected", [(34, 34.0), ("33:30", 33.5), (None, 0.0), (float("nan"), 0.0)]
)
def test_parse_minutes(value, expected):
    assert parse_minutes(value) == pytest.approx(expected)


def test_games_paired_home_away_and_preseason_dropped():
    games = games_from_team_logs(TEAM_LOGS, "2026-27")
    assert len(games) == 1
    g = games[0]
    assert (g["home"], g["away"], g["home_pts"], g["away_pts"]) == ("LAL", "GSW", 112, 108)
    assert g["known_at"] == datetime(2026, 10, 22, 8, 0, tzinfo=UTC)


def test_player_rows_parse_and_skip_preseason():
    rows = player_rows(PLAYER_LOGS)
    assert [r["player_name"] for r in rows] == ["LeBron James", "Stephen Curry"]
    assert rows[1]["minutes"] == pytest.approx(33.5)


class FakeFetcher:
    def __init__(self):
        self.calls = []

    def __call__(self, endpoint, params):
        self.calls.append((endpoint, params))
        if endpoint == "leaguegamelog":
            if params["season_type_all_star"] != "Regular Season":
                return pd.DataFrame()
            return TEAM_LOGS if params["player_or_team_abbreviation"] == "T" else PLAYER_LOGS
        return pd.DataFrame(
            {
                "gameId": ["0022600050", "0042600101"],
                "gameDateEst": ["2026-11-09T00:00:00Z", "2027-04-20T00:00:00Z"],
                "gameDateTimeUTC": ["2026-11-10T00:00:00Z", ""],
                "homeTeam_teamTricode": ["ATL", ""],
                "awayTeam_teamTricode": ["LAL", ""],
            }
        )


def test_ingest_season_is_cached_and_as_of_correct(tmp_path):
    fetcher = FakeFetcher()
    stats = NbaStats(tmp_path / "cache", fetcher=fetcher)
    store = Store(":memory:")
    counts = ingest_season(store, stats, "2026-27")
    assert counts == {"games": 1, "player_games": 2}

    n_calls = len(fetcher.calls)
    ingest_season(store, stats, "2026-27")  # second run reads the cache
    assert len(fetcher.calls) == n_calls

    known = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)
    assert store.player_games_as_of(known - timedelta(seconds=1)) == []
    assert len(store.player_games_as_of(known)) == 2
    assert len(store.results_as_of(known)) == 1


def test_schedule_rows_skip_unset_playoff_slots_and_bound_known_at():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    rows = schedule_rows(FakeFetcher()("schedule", {}), "2026-27", now)
    assert len(rows) == 1
    r = rows[0]
    assert (r["home"], r["away"], r["game_date"]) == ("ATL", "LAL", "2026-11-09")
    assert r["known_at"] == now  # fetched before tip - 24h

    late = schedule_rows(
        FakeFetcher()("schedule", {}), "2026-27", datetime(2026, 12, 1, tzinfo=UTC)
    )
    assert late[0]["known_at"] == r["tip_utc"] - timedelta(days=1)


def test_ingest_schedule_writes_tip_times(tmp_path):
    store = Store(":memory:")
    n = ingest_schedule(store, NbaStats(tmp_path, fetcher=FakeFetcher()), "2026-27")
    assert n == 1
    (row,) = store.query("SELECT * FROM games")
    assert row["tip_utc"].startswith("2026-11-10T00:00:00")
