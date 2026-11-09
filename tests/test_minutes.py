from datetime import date, timedelta

import pytest

from edgebot.model.minutes import (
    PLAYER_CAP,
    TEAM_MINUTES,
    MinutesFeatures,
    MinutesModel,
    build_rosters,
    features_from_history,
    project_team,
    redistribute,
)

DAY = date(2026, 11, 9)


def _f(m: float) -> MinutesFeatures:
    return MinutesFeatures(last=m, avg5=m, avg10=m, season_avg=m, std5=0, games=10, days_rest=2)


ROSTER = {
    1: ("Trae Young", _f(36)),
    2: ("Jalen Johnson", _f(34)),
    3: ("Dyson Daniels", _f(32)),
    4: ("Onyeka Okongwu", _f(30)),
    5: ("Zaccharie Risacher", _f(28)),
    6: ("Bench One", _f(24)),
    7: ("Bench Two", _f(20)),
    8: ("Bench Three", _f(16)),
    9: ("Bench Four", _f(12)),
    10: ("Bench Five", _f(8)),
}


def test_features_only_use_games_before_tonight():
    games = [(DAY - timedelta(days=i), 30.0 + i) for i in range(1, 12)] + [(DAY, 99.0)]
    f = features_from_history(games, DAY)
    assert f.last == 31.0  # yesterday, not tonight's 99
    assert f.avg5 == pytest.approx(33.0)
    assert f.games == 11 and f.days_rest == 1
    assert features_from_history([(DAY, 30.0)], DAY) is None


def test_redistribution_keeps_team_total_and_respects_cap():
    base = {pid: f.avg5 for pid, (_, f) in ROSTER.items()}
    prob = dict.fromkeys(base, 1.0)
    prob[1] = 0.0  # star out
    out = redistribute(base, prob)
    assert out[1] == 0
    assert sum(out.values()) == pytest.approx(TEAM_MINUTES)
    assert max(out.values()) <= PLAYER_CAP + 1e-9
    # starters absorb more of the freed minutes than the deep bench
    assert out[2] - base[2] > out[10] - base[10] > 0


def test_redistribution_when_too_few_players_cannot_reach_240():
    out = redistribute({1: 30.0, 2: 30.0}, {1: 1.0, 2: 1.0})
    assert out == {1: PLAYER_CAP, 2: PLAYER_CAP}


def test_project_team_status_scenarios():
    tm = project_team("ATL", ROSTER, {"Young, Trae": "Questionable", "Ghost Player": "Out"})
    assert tm.has_uncertainty
    assert tm.unmatched_injuries == ["Ghost Player"]
    assert tm.all_sit[1] == 0
    assert tm.all_play[1] > tm.expected[1] > 0
    for scenario in (tm.expected, tm.all_play, tm.all_sit, tm.full_strength):
        assert sum(scenario.values()) == pytest.approx(TEAM_MINUTES)


def test_out_player_gets_zero_in_every_scenario():
    tm = project_team("ATL", ROSTER, {"Trae Young": "Out"})
    assert tm.expected[1] == tm.all_play[1] == tm.all_sit[1] == 0
    assert tm.full_strength[1] > 0
    assert not tm.has_uncertainty


def test_news_minutes_delta_adjusts_base_only_for_that_player():
    plain = project_team("ATL", ROSTER, {})
    limited = project_team("ATL", ROSTER, {}, minutes_delta={"Trae Young": -12})
    assert limited.expected[1] < plain.expected[1]
    assert sum(limited.expected.values()) == pytest.approx(TEAM_MINUTES)


def test_back_to_back_trims_base_minutes():
    m = MinutesModel()
    f = _f(36)
    assert m.base(f, back_to_back=True) < m.base(f)


def test_fit_recovers_weights_and_falls_back_on_small_samples():
    assert MinutesModel.fit([]).weights == MinutesModel().weights
    samples = []
    for i in range(400):
        f = MinutesFeatures(
            last=20 + i % 17,
            avg5=22 + i % 11,
            avg10=24 + i % 7,
            season_avg=25,
            std5=0,
            games=10,
            days_rest=1,
        )
        samples.append((f, f.avg5))  # truth: minutes == avg5
    w = MinutesModel.fit(samples).weights
    assert w[1] == pytest.approx(1.0, abs=0.05)
    assert sum(w) == pytest.approx(1.0)


def test_build_rosters_follows_trades_and_drops_stale_players():
    lines = [
        {
            "player_id": 1,
            "player_name": "Traded Guy",
            "team": "ATL",
            "minutes": 30,
            "game_date": "2026-10-25",
        },
        {
            "player_id": 1,
            "player_name": "Traded Guy",
            "team": "BOS",
            "minutes": 20,
            "game_date": "2026-11-05",
        },
        {
            "player_id": 2,
            "player_name": "Long Injury",
            "team": "ATL",
            "minutes": 25,
            "game_date": "2026-09-01",
        },
        {
            "player_id": 3,
            "player_name": "Regular",
            "team": "ATL",
            "minutes": 33,
            "game_date": "2026-11-07",
        },
        {
            "player_id": 3,
            "player_name": "Regular",
            "team": "ATL",
            "minutes": 99,
            "game_date": "2026-11-09",
        },
    ]
    rosters = build_rosters(lines, DAY)
    assert set(rosters["ATL"]) == {3}
    assert set(rosters["BOS"]) == {1}
    assert rosters["BOS"][1][1].avg5 == 20  # only minutes with the new team
    assert rosters["ATL"][3][1].last == 33  # tonight's line is ignored
