from datetime import UTC, datetime, timedelta

import pytest

from edgebot.ingest.polymarket import Level, OrderBook
from edgebot.trading.executor import OrderRequest, PaperExecutor
from edgebot.trading.risk import RiskState
from edgebot.trading.settlement import settle_positions
from edgebot.trading.state import load_risk_state, log_risk_event, save_risk_state

NOW = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)
TIP = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
SLUG = "aec-nba-lal-atl-2026-11-09"


def _book(state="MARKET_STATE_OPEN"):
    # long side = LAL; short (ATL) asks = 1 - bids
    return OrderBook(
        market_slug=SLUG,
        bids=[Level(price=0.47, qty=100), Level(price=0.46, qty=200)],
        asks=[Level(price=0.48, qty=100), Level(price=0.49, qty=200)],
        state=state,
        fetched_at=NOW,
    )


def _req(**kw):
    base = dict(
        decision_id=None,
        market_slug=SLUG,
        side_team="LAL",
        long_side=True,
        qty=150,
        limit_price=0.49,
        game_date="2026-11-09",
        home="ATL",
        away="LAL",
        tip_utc=TIP,
    )
    return OrderRequest(**{**base, **kw})


def test_paper_fill_walks_the_live_book_and_charges_fees(store):
    ex = PaperExecutor(store, lambda slug: _book(), theta=0.095, clock=lambda: NOW)
    r = ex.submit(_req())
    assert r.status == "filled" and r.filled_qty == 150
    assert r.fills[0][:2] == (0.48, 100) and r.fills[1][:2] == (0.49, 50)
    assert r.fees == pytest.approx(
        round(0.095 * 100 * 0.48 * 0.52, 2) + round(0.095 * 50 * 0.49 * 0.51, 2)
    )
    (pos,) = store.query("SELECT * FROM positions")
    assert pos["qty"] == 150 and pos["cost"] == pytest.approx(0.48 * 100 + 0.49 * 50)
    assert len(store.query("SELECT * FROM fills")) == 2


def test_paper_fill_is_immediate_or_cancel_and_respects_limit(store):
    ex = PaperExecutor(store, lambda slug: _book(), theta=0.095, clock=lambda: NOW)
    r = ex.submit(_req(qty=1000, limit_price=0.48))
    assert r.status == "partial" and r.filled_qty == 100
    assert ex.cancel_all() == 0


def test_short_side_fills_against_long_bids(store):
    ex = PaperExecutor(store, lambda slug: _book(), theta=0.095, clock=lambda: NOW)
    r = ex.submit(_req(side_team="ATL", long_side=False, qty=50, limit_price=0.53))
    assert r.filled_qty == 50 and r.avg_price == pytest.approx(0.53)


def test_closed_market_records_unfilled_order_and_no_position(store):
    ex = PaperExecutor(
        store, lambda slug: _book("MARKET_STATE_HALTED"), theta=0.095, clock=lambda: NOW
    )
    assert ex.submit(_req()).status == "unfilled"
    assert store.query("SELECT * FROM positions") == []
    assert store.query("SELECT status FROM orders")[0]["status"] == "unfilled"


def _game_and_result(store, home_pts, away_pts, known_at):
    store.upsert_game(
        game_id="0022600100", season="2026-27", game_date="2026-11-09", home="ATL", away="LAL",
        known_at=NOW - timedelta(days=30),
    )  # fmt: skip
    store.upsert_result(
        game_id="0022600100", home_pts=home_pts, away_pts=away_pts, known_at=known_at
    )


def test_settlement_pnl_and_clv(store):
    ex = PaperExecutor(store, lambda slug: _book(), theta=0.095, clock=lambda: NOW)
    r = ex.submit(_req(qty=100, limit_price=0.48))
    # closing snapshot before tip: LAL mid 0.52 -> we bought at 0.48 (CLV +0.04)
    store.insert_market_snapshot(
        known_at=TIP - timedelta(minutes=2), market_slug=SLUG, bids=[(0.51, 10)], asks=[(0.53, 10)]
    )
    known = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    _game_and_result(store, home_pts=100, away_pts=110, known_at=known)  # LAL wins

    assert settle_positions(store, known - timedelta(minutes=1)) == []  # result not known yet
    seen = []
    (row,) = settle_positions(store, known, on_settle=seen.append)
    assert row["won"] and row["payout"] == 100
    assert row["pnl"] == pytest.approx(100 - 48 - r.fees)
    assert row["closing_price"] == pytest.approx(0.52)
    assert seen and settle_positions(store, known) == []  # settled once


def test_losing_position_loses_cost_plus_fees(store):
    ex = PaperExecutor(store, lambda slug: _book(), theta=0.095, clock=lambda: NOW)
    r = ex.submit(_req(qty=100, limit_price=0.48))
    known = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    _game_and_result(store, home_pts=120, away_pts=110, known_at=known)  # ATL wins
    (row,) = settle_positions(store, known)
    assert not row["won"] and row["pnl"] == pytest.approx(-48 - r.fees)
    assert row["closing_price"] is None  # no snapshot before tip


def test_risk_state_roundtrip_including_shutdown(store):
    assert load_risk_state(store, 500.0).bankroll == 500.0
    s = RiskState(
        bankroll=900, peak=1200, day_start=950, open_exposure=40,
        games_with_positions={"g1"}, markets_traded={"m1"},
        halted_until=NOW + timedelta(hours=3), shutdown=True, shutdown_reason="drawdown 25%",
    )  # fmt: skip
    save_risk_state(store, s, NOW)
    loaded = load_risk_state(store, 500.0)
    assert loaded == s
    log_risk_event(store, "shutdown", "drawdown 25%", NOW)
    assert store.query("SELECT kind FROM risk_events")[0]["kind"] == "shutdown"


def test_upgrade_keeps_existing_paper_risk_state_and_separates_live(tmp_path):
    import json
    import sqlite3

    from edgebot.db import MIGRATIONS, Store

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for v, sql in enumerate(MIGRATIONS[:5], start=1):
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version VALUES (?)", (v,))
    old = {"bankroll": 900, "peak": 1000, "day_start": 950, "open_exposure": 0,
           "games_with_positions": [], "markets_traded": [], "halted_until": None,
           "shutdown": True, "shutdown_reason": "dd"}  # fmt: skip
    conn.execute(
        "INSERT INTO risk_state VALUES (1, ?, '2026-01-01T00:00:00.000000+00:00')",
        (json.dumps(old),),
    )
    conn.commit()
    conn.close()

    store = Store(path)
    paper = load_risk_state(store, 123.0)
    assert paper.bankroll == 900 and paper.shutdown
    live = load_risk_state(store, 250.0, mode="live")
    assert live.bankroll == 250.0 and not live.shutdown
    save_risk_state(store, live, NOW, mode="live")
    assert load_risk_state(store, 0, mode="paper").bankroll == 900
    store.close()
