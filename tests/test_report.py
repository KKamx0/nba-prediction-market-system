from datetime import UTC, datetime, timedelta

import httpx
import pytest

from edgebot.report import (
    calibration_png,
    compute,
    max_drawdown,
    send_discord,
    to_markdown,
    to_text,
)
from edgebot.timeutil import to_iso

T0 = datetime(2026, 11, 1, 23, 0, tzinfo=UTC)


def _position(store, i, *, won, price=0.50, qty=10.0, closing=0.53, mode="paper"):
    gid = f"00226{i:05d}"
    day = (T0 + timedelta(days=i)).date().isoformat()
    store.upsert_game(game_id=gid, season="2026-27", game_date=day, home="BOS", away="LAL",
                      known_at=T0 - timedelta(days=30), tip_utc=T0 + timedelta(days=i, hours=1))  # fmt: skip
    with store.conn:
        cur = store.conn.execute(
            "INSERT INTO orders (known_at, mode, market_slug, side_team, long_side, qty, limit_price, status)"
            " VALUES (?, ?, ?, 'BOS', 0, ?, ?, 'filled')",
            (to_iso(T0), mode, f"m{i}", qty, price),
        )
        fee = 0.12
        payout = qty if won else 0.0
        store.conn.execute(
            """INSERT INTO positions (order_id, mode, market_slug, game_date, home, away, side_team,
               long_side, qty, cost, fees, opened_at, closing_price, settled_at, won, payout, pnl)
               VALUES (?, ?, ?, ?, 'BOS', 'LAL', 'BOS', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cur.lastrowid, mode, f"m{i}", day, qty, qty * price, fee,
             to_iso(T0 + timedelta(days=i)), closing, to_iso(T0 + timedelta(days=i, hours=10)),
             int(won), payout, payout - qty * price - fee),
        )  # fmt: skip
    return gid, day


def test_empty_database_reports_cleanly(store):
    s = compute(store)
    assert s.n_bets == 0 and s.roi is None and s.clv_mean is None
    assert "bets 0" in to_text(s)
    assert "n/a" in to_markdown(s)


def test_pnl_roi_clv_and_drawdown(store):
    for i, won in enumerate([True, False, True, True]):
        _position(store, i, won=won)
    store.conn.execute(
        "INSERT INTO decisions (known_at, game_date, home, away, action, reasons_json) "
        "VALUES (?, '2026-11-01', 'A', 'B', 'pass', '[]')",
        (to_iso(T0),),
    )
    s = compute(store, starting_bankroll=100)
    assert (s.n_bets, s.n_settled, s.n_passes) == (4, 4, 1)
    per_win, per_loss = 10 - 5 - 0.12, -5 - 0.12
    assert s.pnl == pytest.approx(3 * per_win + per_loss)
    assert s.roi == pytest.approx(s.pnl / (4 * 5.12))
    assert s.clv_mean == pytest.approx(0.03)
    assert s.clv_ci[0] == pytest.approx(0.03) == s.clv_ci[1]  # identical CLVs -> zero width
    assert s.max_drawdown == pytest.approx(5.12 / (100 + per_win))
    assert s.weeks_active == pytest.approx(3 / 7)


def test_paper_and_live_are_reported_separately(store):
    _position(store, 0, won=True, mode="paper")
    _position(store, 1, won=True, mode="live")
    assert compute(store, mode="paper").n_bets == 1
    assert compute(store, mode="live").n_bets == 1


def test_model_vs_market_scoring_uses_last_pre_tip_prediction(store):
    for i in range(30):
        gid, day = _position(store, i, won=True)
        store.upsert_result(game_id=gid, home_pts=100 + (i % 2), away_pts=100 + (1 - i % 2) * 2,
                            known_at=T0 + timedelta(days=i, hours=10))  # fmt: skip
        home_won = (100 + (i % 2)) > (100 + (1 - i % 2) * 2)
        tip = T0 + timedelta(days=i, hours=1)
        for as_of, p in (
            (tip - timedelta(hours=2), 0.5),
            (tip - timedelta(minutes=20), 0.8 if home_won else 0.2),
        ):
            store.save_prediction(game_date=day, home="BOS", away="LAL", as_of=as_of, model="m",
                                  p_home=p, p_market=0.5, known_at=as_of)  # fmt: skip
        # a prediction after tip must be ignored
        store.save_prediction(game_date=day, home="BOS", away="LAL", as_of=tip + timedelta(hours=1),
                              model="m", p_home=0.01 if home_won else 0.99, p_market=0.5,
                              known_at=tip)  # fmt: skip
    s = compute(store)
    assert s.n_scored == 30
    assert s.model_log_loss < s.market_log_loss
    assert s.beats_market is True


def test_markdown_export_is_aggregate_only(store, tmp_path):
    _position(store, 0, won=True)
    md = to_markdown(compute(store), plot_name="cal.png")
    assert "Closing line value" in md and "m0" not in md and "LAL" not in md
    assert calibration_png(store, tmp_path / "x.png") is False  # too few games to plot


def test_max_drawdown():
    assert max_drawdown([], 100) == 0
    assert max_drawdown([10, -22, 5], 100) == pytest.approx(22 / 110)


def test_discord_post_and_error():
    sent = []

    def handler(request):
        sent.append(request.read())
        return httpx.Response(204)

    send_discord(
        "https://discord.test/webhook",
        "hello",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert b"hello" in sent[0]
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(RuntimeError, match="404") as exc:
        send_discord("https://discord.test/secret-hook", "x", bad)
    assert "secret-hook" not in str(exc.value)
