"""Spec for risk.py, written before the implementation.

Limits are enforced in code, not configuration alone:
  * max 2% of bankroll per position, max 10% total open exposure per night
  * at most one position per game; no averaging down; no re-entry (no chasing)
  * daily loss >= 5% -> halt until the next (Eastern) day
  * drawdown >= 20% from peak -> full shutdown, manual reset only
  * kill switch (flag file) blocks everything
"""

from datetime import UTC, datetime, timedelta

import pytest

from edgebot.trading.risk import RiskEngine, RiskLimits, RiskState

NOW = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)  # 6pm ET


def _engine(tmp_path, **state):
    s = RiskState(bankroll=1000.0, peak=1000.0, day_start=1000.0, **state)
    return RiskEngine(RiskLimits(), s, kill_file=tmp_path / "KILL")


def test_defaults_match_the_spec():
    lim = RiskLimits()
    assert (lim.max_position_frac, lim.max_nightly_exposure_frac) == (0.02, 0.10)
    assert (lim.daily_loss_limit_frac, lim.max_drawdown_frac) == (0.05, 0.20)


def test_position_capped_at_two_percent(tmp_path):
    d = _engine(tmp_path).check(game_key="g1", market_slug="m1", stake=80.0, now=NOW)
    assert d.allowed and d.stake == pytest.approx(20.0)
    assert "capped" in " ".join(d.reasons)


def test_nightly_exposure_capped_at_ten_percent(tmp_path):
    eng = _engine(tmp_path, open_exposure=95.0)
    d = eng.check(game_key="g9", market_slug="m9", stake=20.0, now=NOW)
    assert d.allowed and d.stake == pytest.approx(5.0)
    full = _engine(tmp_path, open_exposure=100.0)
    assert not full.check(game_key="g9", market_slug="m9", stake=20.0, now=NOW).allowed


def test_one_position_per_game_and_no_averaging_down(tmp_path):
    eng = _engine(tmp_path, games_with_positions={"g1"}, markets_traded={"m1"})
    d = eng.check(game_key="g1", market_slug="m-other-side", stake=10, now=NOW)
    assert not d.allowed and "game" in d.reasons[0]
    d = eng.check(game_key="g2", market_slug="m1", stake=10, now=NOW)
    assert not d.allowed and "already traded" in d.reasons[0]


def test_daily_loss_limit_halts_until_next_day(tmp_path):
    eng = _engine(tmp_path)
    eng.record_pnl(-49.0, now=NOW)
    assert eng.check(game_key="g", market_slug="m", stake=5, now=NOW).allowed
    eng.record_pnl(-1.0, now=NOW)  # -5.0% on the day
    d = eng.check(game_key="g", market_slug="m", stake=5, now=NOW)
    assert not d.allowed and "daily loss" in d.reasons[0]
    # still halted later tonight (Eastern), released the next Eastern day
    assert not eng.check(
        game_key="g", market_slug="m", stake=5, now=NOW + timedelta(hours=4)
    ).allowed
    tomorrow = NOW + timedelta(days=1)
    eng.new_day(now=tomorrow)
    assert eng.check(game_key="g", market_slug="m", stake=5, now=tomorrow).allowed


def test_drawdown_shutdown_requires_manual_reset(tmp_path):
    eng = _engine(tmp_path)
    eng.state.peak = 1250.0  # bankroll 1000 = 20% below peak
    eng.record_pnl(0.0, now=NOW)
    d = eng.check(game_key="g", market_slug="m", stake=5, now=NOW)
    assert not d.allowed and "drawdown" in d.reasons[0]
    eng.new_day(now=NOW + timedelta(days=3))  # a new day does not clear a shutdown
    assert not eng.check(
        game_key="g", market_slug="m", stake=5, now=NOW + timedelta(days=3)
    ).allowed
    eng.manual_reset(confirm=True)
    assert eng.check(game_key="g", market_slug="m", stake=5, now=NOW + timedelta(days=3)).allowed


def test_manual_reset_needs_explicit_confirmation(tmp_path):
    eng = _engine(tmp_path, shutdown=True)
    with pytest.raises(PermissionError):
        eng.manual_reset(confirm=False)


def test_peak_tracks_new_highs(tmp_path):
    eng = _engine(tmp_path)
    eng.record_pnl(100.0, now=NOW)
    assert eng.state.peak == eng.state.bankroll == 1100.0


def test_kill_switch_file_blocks_everything(tmp_path):
    eng = _engine(tmp_path)
    (tmp_path / "KILL").write_text("stop")
    assert eng.killed()
    d = eng.check(game_key="g", market_slug="m", stake=5, now=NOW)
    assert not d.allowed and "kill switch" in d.reasons[0]


def test_tiny_or_nonpositive_stakes_are_rejected(tmp_path):
    eng = _engine(tmp_path)
    assert not eng.check(game_key="g", market_slug="m", stake=0, now=NOW).allowed
    assert not eng.check(game_key="g", market_slug="m", stake=0.5, now=NOW).allowed  # below min


def test_record_fill_updates_exposure_and_blocks_repeat(tmp_path):
    eng = _engine(tmp_path)
    eng.record_fill(game_key="g1", market_slug="m1", cost=20.0)
    assert eng.state.open_exposure == 20.0
    assert not eng.check(game_key="g1", market_slug="m2", stake=5, now=NOW).allowed
