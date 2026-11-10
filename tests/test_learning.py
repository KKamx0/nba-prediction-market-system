import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from edgebot.ingest.polymarket import Level, Moneyline, OrderBook
from edgebot.learning.minutes_bias import (
    MinutesBiasConfig,
    learn_bias,
    log_projections,
    minutes_bias_as_of,
    projection_errors,
)
from edgebot.learning.postmortem import review_settled, verdict, verdict_counts
from edgebot.learning.retrain import compare, retrain_if_better
from edgebot.learning.segments import (
    LearningConfig,
    SegmentPolicy,
    context,
    learn_segments,
    settled_bets,
)
from edgebot.learning.summary import to_markdown, to_text
from edgebot.model.minutes import TeamMinutes
from edgebot.timeutil import to_iso
from edgebot.trading.decision import GameView, decide

T0 = datetime(2026, 11, 1, 23, 0, tzinfo=UTC)


# -- segments --------------------------------------------------------------------


def _bets(n, clv, **ctx):
    base = {"team": "BOS", "opponent": "NYK", "venue": "home", "role": "favorite",
            "weekday": "Monday", "rest": "rested", "pnl": 1.0}  # fmt: skip
    return [{**base, **ctx, "clv": clv + (i % 3 - 1) * 0.004} for i in range(n)]


def test_segments_need_evidence_before_acting():
    stats = {(s.dimension, s.value): s for s in learn_segments(_bets(10, -0.05))}
    assert stats[("team", "BOS")].action == "insufficient"  # 10 bets: not enough


def test_segment_actions_follow_shrunk_clv_bounds():
    bets = (
        _bets(60, -0.04, team="BKN", opponent="A")  # consistently beaten by the close
        + _bets(60, -0.012, team="CHA", opponent="B")  # mildly negative
        + _bets(60, 0.03, team="OKC", opponent="C")  # consistently beating the close
        + _bets(60, 0.0, team="DEN", opponent="D")  # nothing there
    )
    stats = {(s.dimension, s.value): s for s in learn_segments(bets)}
    assert stats[("team", "BKN")].action == "avoid"
    assert stats[("team", "CHA")].action == "raise_edge"
    assert stats[("team", "OKC")].action == "favor"
    assert stats[("team", "DEN")].action == "neutral"
    # shrinkage: the estimate is pulled toward zero relative to the raw mean
    okc = stats[("team", "OKC")]
    assert 0 < okc.estimate < okc.mean_clv


def test_policy_combines_rules_conservatively():
    bets = (
        _bets(60, -0.04, team="BKN")
        + _bets(60, 0.03, team="OKC", weekday="Friday")
        + _bets(60, -0.012, team="CHA", weekday="Friday")
    )
    policy = SegmentPolicy(learn_segments(bets))
    ctx = dict(
        team="BKN", opponent="X", venue="home", role="favorite", weekday="Monday", rest="rested"
    )
    thr, notes = policy.threshold(ctx, 0.04)
    assert thr is None and "avoid team=BKN" in notes[0]

    # segments overlap: here every BKN bet was on a Monday, so weekday=Monday is
    # (correctly) an avoid rule too. Use unmatched values for the other dimensions.
    okc = {d: "?" for d in ctx} | {"team": "OKC"}
    thr, _ = policy.threshold(okc, 0.04)
    assert thr == pytest.approx(max(0.04 * 0.85, 0.03))

    assert policy.threshold({d: "?" for d in ctx}, 0.04)[0] == pytest.approx(0.04)


def test_raise_beats_favor_and_floor_holds():
    cfg = LearningConfig()
    # OKC bets (home) beat the close; another team's away bets mildly lose to it
    bets = _bets(80, 0.03, team="OKC") + _bets(80, -0.012, team="X", venue="away", opponent="Y")
    policy = SegmentPolicy(learn_segments(bets, cfg), cfg)
    assert policy.active[("team", "OKC")].action == "favor"
    assert policy.active[("venue", "away")].action == "raise_edge"
    other = {"opponent": "Z", "role": "?", "weekday": "?", "rest": "?"}
    both = {**other, "team": "OKC", "venue": "away"}
    assert policy.threshold(both, 0.04)[0] == pytest.approx(0.04 * cfg.raise_mult)  # raise wins
    only_favor = {**other, "team": "OKC", "venue": "?"}
    assert policy.threshold(only_favor, 0.02)[0] == pytest.approx(cfg.min_edge_floor)  # floor


def test_context_dimensions():
    c = context(
        side_team="LAL",
        home="BOS",
        away="LAL",
        game_date="2026-11-09",
        price=0.42,
        back_to_back=True,
    )
    assert c == {"team": "LAL", "opponent": "BOS", "venue": "away", "role": "underdog",
                 "weekday": "Monday", "rest": "back-to-back"}  # fmt: skip


def _position(store, i, *, side="BOS", price=0.5, closing=0.53, won=True, home="BOS", away="LAL",
              settled=True, day=None):  # fmt: skip
    day = day or (T0 + timedelta(days=i)).date().isoformat()
    opened = datetime.fromisoformat(day + "T22:00:00+00:00")
    with store.conn:
        oid = store.conn.execute(
            "INSERT INTO orders (known_at, mode, market_slug, side_team, long_side, qty, limit_price,"
            " status) VALUES (?, 'paper', ?, ?, 1, 10, ?, 'filled')",
            (to_iso(opened), f"m{i}", side, price),
        ).lastrowid
        return store.conn.execute(
            """INSERT INTO positions (order_id, mode, market_slug, game_date, home, away, side_team,
               long_side, tip_utc, qty, cost, fees, opened_at, closing_price, settled_at, won, payout, pnl)
               VALUES (?, 'paper', ?, ?, ?, ?, ?, 1, ?, 10, ?, 0.2, ?, ?, ?, ?, ?, ?)""",
            (oid, f"m{i}", day, home, away, side, to_iso(opened + timedelta(hours=2)), 10 * price,
             to_iso(opened), closing, to_iso(opened + timedelta(hours=12)) if settled else None,
             int(won), 10 if won else 0, (10 if won else 0) - 10 * price - 0.2),
        ).lastrowid  # fmt: skip


def test_settled_bets_reads_context_and_respects_as_of(store):
    pid = _position(store, 0, side="LAL", price=0.4, closing=0.44)
    store.save_features(game_date="2026-11-01", home="BOS", away="LAL",
                        as_of=datetime(2026, 11, 1, 21, tzinfo=UTC), version="v1",
                        values={"home_b2b": 0, "away_b2b": 1}, known_at=T0)  # fmt: skip
    (b,) = settled_bets(store, T0 + timedelta(days=2))
    assert b["clv"] == pytest.approx(0.04)
    assert (b["team"], b["venue"], b["role"], b["rest"]) == (
        "LAL",
        "away",
        "underdog",
        "back-to-back",
    )
    assert settled_bets(store, T0) == []  # not settled yet at T0
    assert pid


# -- decisions use the learned thresholds ----------------------------------------------

ML = Moneyline(event_slug="nba-lal-atl-2026-11-09", market_slug="aec-nba-lal-atl-2026-11-09",
               start_utc=datetime(2026, 11, 10, 0, 0, tzinfo=UTC), home="ATL", away="LAL",
               long_team="LAL", fee_coefficient=0.095, min_tick=0.01)  # fmt: skip
BOOK = OrderBook(market_slug=ML.market_slug, bids=[Level(price=0.47, qty=5000)],
                 asks=[Level(price=0.48, qty=5000)], state="MARKET_STATE_OPEN",
                 fetched_at=datetime(2026, 11, 9, 22, tzinfo=UTC))  # fmt: skip


def _view(p_home):
    return GameView(market=ML, book=BOOK, p_home=p_home, p_home_play=p_home, p_home_sit=p_home,
                    has_uncertainty=False)  # fmt: skip


def test_avoid_rule_skips_a_bet_the_model_likes():
    base = decide(_view(0.62), bankroll=1000, theta=0.095)
    assert base.action == "bet" and base.side_team == "ATL"
    skip = decide(_view(0.62), bankroll=1000, theta=0.095,
                  min_edge_for=lambda team, price: (None, ["learned avoid team=ATL"]) if team == "ATL" else (0.04, []))  # fmt: skip
    assert skip.action == "pass" and any("learned avoid" in r for r in skip.reasons)


def test_raised_threshold_blocks_and_favor_allows():
    # ATL edge here is about 6%: passes at 4%, fails at a raised 8%
    raised = decide(
        _view(0.62), bankroll=1000, theta=0.095, min_edge_for=lambda t, p: (0.08, ["raise"])
    )
    assert raised.action == "pass" and "raise" in raised.reasons
    # ATL edge about 3.5%: fails the usual 4%, clears a favored 3.4%
    assert decide(_view(0.594), bankroll=1000, theta=0.095).action == "pass"
    favored = decide(
        _view(0.594), bankroll=1000, theta=0.095, min_edge_for=lambda t, p: (0.034, ["favor"])
    )
    assert favored.action == "bet" and "favor" in favored.reasons


# -- minutes bias ---------------------------------------------------------------


def _game(store, gid, day, home="BOS", away="LAL", known=None):
    store.upsert_game(game_id=gid, season="2026-27", game_date=day, home=home, away=away,
                      known_at=T0 - timedelta(days=30))  # fmt: skip
    store.upsert_result(game_id=gid, home_pts=100, away_pts=90, known_at=known)


def _tm(minutes, names=None):
    names = names or {pid: f"Player {pid}" for pid in minutes}
    return TeamMinutes(team="BOS", expected=minutes, all_play=minutes, all_sit=minutes,
                       full_strength=minutes, names=names)  # fmt: skip


def test_minutes_errors_count_dnp_as_zero_and_use_latest_projection(store):
    day = "2026-11-01"
    known = datetime(2026, 11, 2, 8, tzinfo=UTC)
    _game(store, "g1", day, known=known)
    log_projections(store, day, "BOS", _tm({1: 30.0, 2: 20.0}), T0 - timedelta(hours=3))
    log_projections(store, day, "BOS", _tm({1: 32.0, 2: 20.0}), T0)  # latest wins
    store.upsert_player_games([{"game_id": "g1", "player_id": 1, "player_name": "Player 1", "team": "BOS",
                                "minutes": 36.0, "known_at": known}])  # fmt: skip
    assert projection_errors(store, known - timedelta(seconds=1)) == []  # game not final yet
    errs = {e["player_id"]: e for e in projection_errors(store, known)}
    assert errs[1]["projected"] == 32.0 and errs[1]["actual"] == 36.0
    assert errs[2]["actual"] == 0.0  # projected 20 but didn't appear: a DNP


def test_bias_is_shrunk_capped_and_needs_samples():
    errs = [{"player_id": 1, "player_name": "Rested Vet", "projected": 30, "actual": 20}] * 12
    errs += [{"player_id": 2, "player_name": "New Starter", "projected": 10, "actual": 40}] * 12
    errs += [{"player_id": 3, "player_name": "Two Games", "projected": 20, "actual": 30}] * 2
    b = learn_bias(errs, MinutesBiasConfig(prior_n=8, min_n=3, cap=6))
    assert b["Rested Vet"]["bias"] == pytest.approx(-120 / 20)  # shrunk: -10 avg -> -6
    assert b["New Starter"]["bias"] == pytest.approx(6.0)  # capped
    assert "Two Games" not in b


def test_learned_bias_feeds_features_only_once_known():
    from edgebot.db import Store
    from edgebot.model.features import FeatureBuilder
    from edgebot.model.strength import RatingsBook, rosters_as_of
    from tests.synthetic import load_into_store, make_league

    store = Store(":memory:")
    load_into_store(store, make_league(n_games=600, seed=41, start=date(2025, 10, 21)), "2025-26")
    as_of = datetime(2025, 12, 5, 23, 0, tzinfo=UTC)
    ratings = RatingsBook(store).as_of(as_of, date(2025, 12, 5))
    roster = rosters_as_of(store, date(2025, 12, 5), as_of)["BOS"]
    star = max((p for p in roster if roster[p][1].avg10 >= 20), key=ratings.net)
    base = FeatureBuilder(store).build(
        game_date="2025-12-05", home="BOS", away="LAL", as_of=as_of, save=False
    )
    # pretend we over-projected the star by 15 minutes in four recent BOS games
    games = store.query("SELECT game_id, game_date FROM games WHERE home = 'BOS' AND game_date < '2025-12-05' "
                        "ORDER BY game_date DESC LIMIT 4")  # fmt: skip
    for g in games:
        actual = store.query("SELECT minutes FROM player_games WHERE game_id = ? AND player_id = ?",
                             (g["game_id"], star))  # fmt: skip
        played = actual[0]["minutes"] if actual else 0.0
        log_projections(store, g["game_date"], "BOS", _tm({star: min(played + 15, 48)}, {star: roster[star][0]}),
                        datetime.fromisoformat(g["game_date"] + "T20:00:00+00:00"))  # fmt: skip
    assert minutes_bias_as_of(store, as_of)[roster[star][0]] < 0
    after = FeatureBuilder(store).build(
        game_date="2025-12-05", home="BOS", away="LAL", as_of=as_of, save=False
    )
    before_results = minutes_bias_as_of(store, datetime(2025, 11, 1, tzinfo=UTC))
    store.close()
    assert after.strength_diff < base.strength_diff  # fewer minutes for the best player
    assert before_results == {}  # nothing learnable before those games were final


# -- post-mortems ---------------------------------------------------------------


@pytest.mark.parametrize(
    "clv,won,surprises,expected",
    [
        (0.03, False, [], "good bet, lost (variance)"),
        (0.03, True, [], "good bet, won"),
        (-0.01, True, [], "lucky win"),
        (-0.01, False, [], "bad bet, lost"),
        (-0.05, True, [], "beaten by the market"),
        (0.03, False, ["X sat"], "availability miss"),
        (None, True, [], "won (no closing price)"),
    ],
)
def test_verdicts(clv, won, surprises, expected):
    assert verdict(clv, won, surprises) == expected


def test_review_flags_a_scratched_starter_once(store):
    day = (T0 + timedelta(days=0)).date().isoformat()
    _game(store, "g9", day, known=T0 + timedelta(hours=12))
    pid = _position(store, 0, won=False, closing=0.55)
    log_projections(store, day, "BOS", _tm({7: 34.0}, {7: "Star Guard"}), T0 - timedelta(hours=1))
    (r,) = review_settled(store, T0 + timedelta(days=1))
    assert r["position_id"] == pid and r["verdict"] == "availability miss"
    assert "Star Guard (BOS) projected 34 min, sat" in r["surprises"][0]
    assert review_settled(store, T0 + timedelta(days=1)) == []
    assert verdict_counts(store)["availability miss"] == 1


# -- retraining -------------------------------------------------------------------


class Const:
    def __init__(self, p):
        self.p = p

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.full(len(X), self.p)


def _frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2026-10-21", periods=n // 5, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({"game_date": np.repeat(days, 5)[:n], "game_id": [f"g{i}" for i in range(n)],
                       "market_logit": rng.normal(0, 1, n)})  # fmt: skip
    df["y"] = (rng.uniform(size=n) < 1 / (1 + np.exp(-df["market_logit"]))).astype(int)
    return df


def test_compare_scores_both_models_on_unseen_games_only():
    from edgebot.model.game_model import MarketOnly

    df = _frame()
    n, champ, chall = compare(
        df, Const(0.5), lambda: MarketOnly(), train_end="2026-12-10", min_new=30
    )
    assert n == len(df[df["game_date"] > "2026-12-10"])
    assert chall < champ  # a real model beats a coin-flip champion
    n2, c2, _ = compare(df, Const(0.5), lambda: MarketOnly(), train_end="2027-12-31", min_new=30)
    assert (n2, c2) == (0, None)


def _setup_models(tmp_path, champion, train_end):
    from edgebot.model.game_model import save_model

    model_path, choice = (
        tmp_path / "models" / "game_model.pkl",
        tmp_path / "data" / "model_choice.json",
    )
    save_model(champion, model_path)
    choice.parent.mkdir(parents=True)
    choice.write_text(json.dumps({"model": "market", "train_end": train_end}))
    return model_path, choice


def test_retrain_promotes_only_when_it_would_have_done_better(store, tmp_path, monkeypatch):
    from edgebot.learning import retrain as rt
    from edgebot.model.game_model import MarketOnly

    df = _frame()
    monkeypatch.setattr(rt, "update_dataset", lambda store, path: df)

    model_path, choice = _setup_models(tmp_path, Const(0.5), "2026-12-10")
    r = retrain_if_better(store, cache_path=tmp_path / "c.csv", model_path=model_path, choice_path=choice,
                          now=T0, min_new=30)  # fmt: skip
    assert r.promoted and r.challenger_log_loss < r.champion_log_loss
    assert json.loads(choice.read_text())["train_end"] == df["game_date"].max()
    assert (tmp_path / "models" / "game_model.prev.pkl").exists()

    # now the champion is a good fitted model; a retrain with noise-only labels must not replace it
    good = MarketOnly().fit(df, df["y"].to_numpy())
    model_path, choice = _setup_models(tmp_path / "b", good, "2026-12-10")
    noisy = df.copy()
    noisy["y"] = np.random.default_rng(5).integers(0, 2, len(df))
    monkeypatch.setattr(rt, "update_dataset", lambda store, path: noisy)
    r2 = retrain_if_better(store, cache_path=tmp_path / "c.csv", model_path=model_path, choice_path=choice,
                           now=T0, min_new=30)  # fmt: skip
    rows = store.query("SELECT promoted, reason FROM model_versions ORDER BY id")
    assert len(rows) == 2 and rows[0]["promoted"] == 1
    assert r2.promoted == bool(rows[1]["promoted"])


def test_retrain_waits_for_enough_new_games(store, tmp_path, monkeypatch):
    from edgebot.learning import retrain as rt

    monkeypatch.setattr(rt, "update_dataset", lambda store, path: _frame(50))
    model_path, choice = _setup_models(tmp_path, Const(0.5), "2026-10-25")
    r = retrain_if_better(
        store, cache_path=tmp_path / "c.csv", model_path=model_path, choice_path=choice, now=T0
    )
    assert not r.promoted and "new games" in r.reason


# -- summary --------------------------------------------------------------------


def test_summary_text_and_markdown(store):
    assert "no settled bets yet" in to_text(store, T0)
    for i in range(25):
        _position(store, i, side="BOS", closing=0.46)  # bought 0.50, closed 0.46: bad CLV
    review_settled(store, T0 + timedelta(days=40))
    text = to_text(store, T0 + timedelta(days=40))
    assert "post-mortems (25 bets)" in text and "team=BOS" in text
    md = to_markdown(store, T0 + timedelta(days=40))
    assert "## What it has learned" in md and "beaten by the market" in md
