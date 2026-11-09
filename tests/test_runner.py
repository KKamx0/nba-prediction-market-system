from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from edgebot.db import Store
from edgebot.ingest.polymarket import Level, Moneyline, OrderBook
from edgebot.model.features import MODEL_FEATURES
from edgebot.runner import KillSwitchError, Runner, RunnerConfig, game_key, predict_scenarios
from edgebot.trading.executor import PaperExecutor
from edgebot.trading.risk import RiskEngine, RiskLimits
from edgebot.trading.state import load_risk_state
from tests.synthetic import load_into_store, make_league

TIP = datetime(2025, 12, 6, 0, 30, tzinfo=UTC)  # 7:30pm ET Dec 5
NOW = TIP - timedelta(hours=2)
ML = Moneyline(
    event_slug="nba-lal-bos-2025-12-05",
    market_slug="aec-nba-lal-bos-2025-12-05",
    start_utc=TIP,
    home="BOS",
    away="LAL",
    long_team="LAL",
    fee_coefficient=0.095,
    min_tick=0.01,
)


class StubModel:
    """Market logit plus a fixed bump: a model that 'likes' the home team."""

    def __init__(self, bump=0.35):
        self.bump = bump

    def predict(self, X):
        assert list(X.columns) == MODEL_FEATURES
        return 1 / (1 + np.exp(-(X["market_logit"].to_numpy() + self.bump)))


class FakePM:
    def __init__(self, moneylines=(ML,)):
        self.moneylines = list(moneylines)

    def nba_moneylines(self):
        return self.moneylines

    def book(self, slug):
        # LAL (long) 0.44 / 0.45 -> BOS (short) can be bought at 0.56
        return OrderBook(
            market_slug=slug,
            bids=[Level(price=0.44, qty=5000)],
            asks=[Level(price=0.45, qty=5000)],
            state="MARKET_STATE_OPEN",
            fetched_at=NOW,
        )


@pytest.fixture
def env(tmp_path):
    store = Store(":memory:")
    load_into_store(store, make_league(n_games=600, seed=31, start=date(2025, 10, 21)), "2025-26")
    # sportsbook line for tonight, known an hour before the first cycle: BOS ~0.555
    store.insert_odds_quotes(
        [
            {
                "known_at": NOW - timedelta(hours=1),
                "source": "odds_api",
                "bookmaker": "book",
                "kind": "live",
                "game_date": "2025-12-05",
                "home": "BOS",
                "away": "LAL",
                "home_dec": 1.72,
                "away_dec": 2.15,
            }
        ]
    )
    clock = {"now": NOW}
    risk = RiskEngine(RiskLimits(), load_risk_state(store, 1000.0), kill_file=tmp_path / "KILL")
    pm = FakePM()
    runner = Runner(
        store=store,
        model=StubModel(),
        model_name="stub",
        executor=PaperExecutor(store, pm.book, theta=0.095, clock=lambda: clock["now"]),
        risk=risk,
        polymarket=pm,
        config=RunnerConfig(),
        clock=lambda: clock["now"],
    )
    yield store, runner, clock, tmp_path
    store.close()


def test_cycle_bets_logs_and_persists(env):
    store, runner, _, _ = env
    r = runner.cycle()
    assert (r.snapshots, r.evaluated, r.bets) == (1, 1, 1), r.errors
    (dec,) = store.query("SELECT * FROM decisions")
    assert dec["action"] == "bet" and dec["side_team"] == "BOS"
    assert dec["edge"] >= 0.04
    (pos,) = store.query("SELECT * FROM positions")
    assert pos["side_team"] == "BOS" and pos["long_side"] == 0 and pos["mode"] == "paper"
    assert pos["cost"] <= 0.02 * 1000  # 2% cap
    assert len(store.query("SELECT * FROM predictions")) == 1
    assert load_risk_state(store, 0).games_with_positions == {game_key("2025-12-05", "BOS", "LAL")}


def test_no_reevaluation_until_due_then_risk_blocks_a_second_bet(env):
    store, runner, clock, _ = env
    runner.cycle()
    assert runner.cycle().evaluated == 0  # not due yet
    clock["now"] = NOW + timedelta(minutes=31)
    assert runner.cycle().evaluated == 1
    last = store.query("SELECT action, reasons_json FROM decisions ORDER BY id DESC LIMIT 1")[0]
    assert last["action"] == "pass" and "one correlated position" in last["reasons_json"]
    assert len(store.query("SELECT * FROM positions")) == 1


def test_games_outside_the_window_are_snapshotted_but_not_evaluated(env):
    store, runner, clock, _ = env
    clock["now"] = TIP - timedelta(hours=8)
    r = runner.cycle()
    assert (r.snapshots, r.evaluated) == (1, 0)
    clock["now"] = TIP - timedelta(minutes=5)  # too close to tip
    assert runner.cycle().evaluated == 0


def test_missing_sportsbook_line_is_a_logged_pass(env):
    store, runner, _, _ = env
    store.conn.execute("DELETE FROM odds_quotes")
    runner.cycle()
    (dec,) = store.query("SELECT action, reasons_json FROM decisions")
    assert dec["action"] == "pass" and "sportsbook" in dec["reasons_json"]


def test_kill_switch_stops_the_loop_and_logs(env):
    store, runner, _, tmp_path = env
    (tmp_path / "KILL").write_text("stop")
    with pytest.raises(KillSwitchError):
        runner.cycle()
    assert store.query("SELECT kind FROM risk_events")[0]["kind"] == "kill_switch"
    runner.run(interval_s=0, sleep=lambda s: None)  # returns instead of looping


def test_settlement_updates_bankroll(env):
    store, runner, clock, _ = env
    runner.cycle()
    (pos,) = store.query("SELECT * FROM positions")
    known = datetime(2025, 12, 6, 8, 0, tzinfo=UTC)
    store.upsert_game(
        game_id="0099900001", season="2025-26", game_date="2025-12-05", home="BOS", away="LAL",
        known_at=NOW - timedelta(days=10),
    )  # fmt: skip
    store.upsert_result(game_id="0099900001", home_pts=110, away_pts=100, known_at=known)
    clock["now"] = known
    assert runner.cycle().settled == 1
    expected = 1000 + pos["qty"] - pos["cost"] - pos["fees"]
    assert runner.risk.state.bankroll == pytest.approx(expected)
    assert runner.risk.state.open_exposure == pytest.approx(0, abs=1e-9)


def test_ingestion_failures_do_not_stop_the_cycle(env):
    store, runner, _, _ = env

    def boom():
        raise ConnectionError("network down")

    runner.injuries = boom
    r = runner.cycle()
    assert r.bets == 1 and any("injuries" in e for e in r.errors)


def test_predict_scenarios_moves_with_strength():
    class Linear:
        def predict(self, X):
            return 1 / (1 + np.exp(-0.1 * X["strength_diff"].to_numpy()))

    from edgebot.model.features import GameFeatures

    f = GameFeatures(
        game_date="2025-12-05", home="BOS", away="LAL", as_of=NOW, market_prob=0.5,
        market_logit=0.0, strength_diff=2.0, strength_diff_play=5.0, strength_diff_sit=-3.0,
        full_strength_diff=5.0, injury_drag_diff=3.0, pace=99, rest_diff=0, home_b2b=0,
        away_b2b=0, travel_home_kkm=0, travel_away_kkm=1, tz_shift_away=0, density_diff=0,
        consistency_home=10, consistency_away=10, has_uncertainty=1,
    )  # fmt: skip
    p, play, sit = predict_scenarios(Linear(), f)
    assert sit < p < play


def test_results_pulled_once_per_day_after_box_scores_are_known(env):
    _, runner, clock, _ = env
    calls = []
    runner.results = lambda: calls.append(clock["now"]) or 0
    clock["now"] = datetime(2025, 12, 6, 7, 0, tzinfo=UTC)
    runner.cycle()
    assert calls == []  # before 09:00 UTC
    clock["now"] = datetime(2025, 12, 6, 9, 30, tzinfo=UTC)
    runner.cycle()
    runner.cycle()
    assert len(calls) == 1


def test_daily_report_sent_once_after_settlement_window(env):
    _, runner, clock, _ = env
    sent = []
    runner.daily_report = lambda: sent.append(clock["now"]) or True
    clock["now"] = datetime(2025, 12, 6, 14, 0, tzinfo=UTC)
    runner.cycle()
    clock["now"] = datetime(2025, 12, 6, 15, 30, tzinfo=UTC)
    runner.cycle()
    runner.cycle()
    assert len(sent) == 1
