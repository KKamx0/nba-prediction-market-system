"""Spec for the paper -> live gate, written before the implementation.

LiveExecutor refuses to run unless ALL hold, checked from the database:
  * >= 300 paper bets over >= 6 weeks of the current season
  * positive average CLV with a 95% CI that excludes zero
  * model log loss better than the market-only baseline
  * positive ROI after fees
  * max paper drawdown < 15%
plus LIVE_TRADING=true, --confirm-live, a live
bankroll cap, and Polymarket API keys.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from edgebot.config import Secret, Settings
from edgebot.gate import evaluate_gate
from edgebot.timeutil import to_iso

SEASON_START = datetime(2026, 10, 21, 23, 0, tzinfo=UTC)
NOW = datetime(2027, 1, 20, 12, 0, tzinfo=UTC)


def _ready_settings(**kw):
    base = dict(
        live_trading=True,
        live_bankroll_cap=250.0,
        polymarket_key_id=Secret("key-id"),
        polymarket_secret_key=Secret("secret"),
    )
    return Settings(**{**base, **kw})


def _seed(
    store, n=320, days=70, win_every=2, clv=0.02, pnl_win=5.6, pnl_loss=-5.2, model_better=True
):
    """n settled paper positions spread over `days`, plus scored predictions."""
    for i in range(n):
        opened = SEASON_START + timedelta(days=days * i / n)
        day = opened.date().isoformat()
        gid = f"00226{i:05d}"
        home_won = i % 2 == 0
        store.upsert_game(game_id=gid, season="2026-27", game_date=day, home="BOS", away=f"A{i}",
                          known_at=SEASON_START - timedelta(days=60), tip_utc=opened + timedelta(hours=1))  # fmt: skip
        store.upsert_result(game_id=gid, home_pts=110 if home_won else 100,
                            away_pts=100 if home_won else 110, known_at=opened + timedelta(hours=10))  # fmt: skip
        p_model = (0.7 if home_won else 0.3) if model_better else 0.5
        store.save_prediction(game_date=day, home="BOS", away=f"A{i}", as_of=opened, model="m",
                              p_home=p_model, p_market=0.5, known_at=opened)  # fmt: skip
        won = i % win_every == 0
        with store.conn:
            cur = store.conn.execute(
                "INSERT INTO orders (known_at, mode, market_slug, side_team, long_side, qty, limit_price, status)"
                " VALUES (?, 'paper', ?, 'BOS', 1, 10, 0.5, 'filled')",
                (to_iso(opened), f"m{i}"),
            )
            store.conn.execute(
                """INSERT INTO positions (order_id, mode, market_slug, game_date, home, away, side_team,
                   long_side, qty, cost, fees, opened_at, closing_price, settled_at, won, payout, pnl)
                   VALUES (?, 'paper', ?, ?, 'BOS', ?, 'BOS', 1, 10, 5, 0.2, ?, ?, ?, ?, ?, ?)""",
                (cur.lastrowid, f"m{i}", day, f"A{i}", to_iso(opened), 0.5 + clv + (i % 3 - 1) * 0.01,
                 to_iso(opened + timedelta(hours=10)), int(won), 10 if won else 0,
                 pnl_win if won else pnl_loss),
            )  # fmt: skip


def _by_name(result):
    return {c.name: c for c in result.checks}


def test_everything_in_place_passes(store):
    _seed(store)
    r = evaluate_gate(store, _ready_settings(), confirm_live=True, now=NOW, paper_bankroll=1000)
    assert r.passed, [c for c in r.checks if not c.passed]


def test_empty_database_fails_every_track_record_check(store):
    r = evaluate_gate(store, _ready_settings(), confirm_live=True, now=NOW, paper_bankroll=1000)
    c = _by_name(r)
    assert not r.passed
    for name in ("paper bets", "weeks of paper trading", "closing line value", "log loss vs market",
                 "roi after fees", "max drawdown"):  # fmt: skip
        assert not c[name].passed, name


@pytest.mark.parametrize(
    "seed_kwargs,failing",
    [
        ({"n": 250}, "paper bets"),
        ({"days": 30}, "weeks of paper trading"),
        ({"clv": -0.01}, "closing line value"),
        ({"clv": 0.0}, "closing line value"),  # CI straddles zero
        ({"model_better": False}, "log loss vs market"),
        ({"pnl_win": 4.0, "pnl_loss": -5.2}, "roi after fees"),
    ],
)
def test_each_track_record_condition_is_required(store, seed_kwargs, failing):
    _seed(store, **seed_kwargs)
    r = evaluate_gate(store, _ready_settings(), confirm_live=True, now=NOW, paper_bankroll=1000)
    assert not r.passed
    assert not _by_name(r)[failing].passed


def test_drawdown_of_15_percent_or_more_fails(store):
    _seed(store, n=320, win_every=1)  # all wins...
    with store.conn:  # ...then a cluster of losses deep enough for a >15% drawdown
        store.conn.execute(
            "UPDATE positions SET pnl = -60, won = 0, payout = 0 WHERE id BETWEEN 200 AND 210"
        )
    r = evaluate_gate(store, _ready_settings(), confirm_live=True, now=NOW, paper_bankroll=1000)
    assert not _by_name(r)["max drawdown"].passed


def test_only_current_season_bets_count(store):
    _seed(store)
    later = datetime(
        2027, 11, 1, tzinfo=UTC
    )  # next season: last season's record doesn't carry over
    r = evaluate_gate(store, _ready_settings(), confirm_live=True, now=later, paper_bankroll=1000)
    assert not _by_name(r)["paper bets"].passed


@pytest.mark.parametrize(
    "settings_kw,confirm,failing",
    [
        ({"live_trading": False}, True, "LIVE_TRADING=true"),
        ({}, False, "--confirm-live"),
        ({"live_bankroll_cap": 0.0}, True, "live bankroll cap"),
        ({"polymarket_secret_key": Secret("")}, True, "polymarket api keys"),
    ],
)
def test_operator_switches_are_all_required(store, settings_kw, confirm, failing):
    _seed(store)
    r = evaluate_gate(
        store, _ready_settings(**settings_kw), confirm_live=confirm, now=NOW, paper_bankroll=1000
    )
    assert not r.passed
    assert not _by_name(r)[failing].passed


def test_report_text_lists_every_check(store):
    r = evaluate_gate(store, _ready_settings(), confirm_live=True, now=NOW, paper_bankroll=1000)
    text = r.render()
    assert text.count("\n") + 1 == len(r.checks) + 1  # one line per check plus verdict
    assert "BLOCKED" in text


def test_season_start_helper():
    from edgebot.gate import season_start

    assert season_start(date(2027, 1, 20)) == date(2026, 8, 1)
    assert season_start(date(2026, 9, 1)) == date(2026, 8, 1)
