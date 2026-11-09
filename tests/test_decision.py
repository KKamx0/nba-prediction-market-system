from datetime import UTC, datetime

import pytest

from edgebot.ingest.polymarket import Level, Moneyline, OrderBook
from edgebot.trading.decision import GameView, decide

NOW = datetime(2026, 11, 9, 22, 0, tzinfo=UTC)
ML = Moneyline(
    event_slug="nba-lal-atl-2026-11-09",
    market_slug="aec-nba-lal-atl-2026-11-09",
    start_utc=datetime(2026, 11, 10, 0, 0, tzinfo=UTC),
    home="ATL",
    away="LAL",
    long_team="LAL",
    fee_coefficient=0.095,
    min_tick=0.01,
)


def _book(bid=0.47, ask=0.48, depth=5000.0, state="MARKET_STATE_OPEN"):
    return OrderBook(
        market_slug=ML.market_slug,
        bids=[Level(price=bid, qty=depth), Level(price=bid - 0.01, qty=depth)],
        asks=[Level(price=ask, qty=depth), Level(price=ask + 0.01, qty=depth)],
        state=state,
        fetched_at=NOW,
    )


def _view(p_home, play=None, sit=None, uncertain=False, book=None):
    return GameView(
        market=ML,
        book=book or _book(),
        p_home=p_home,
        p_home_play=p_home if play is None else play,
        p_home_sit=p_home if sit is None else sit,
        has_uncertainty=uncertain,
    )


def test_passes_when_model_agrees_with_market():
    # LAL (long) mid 0.475 -> ATL 0.525; model ATL 0.53: no edge after fees
    d = decide(_view(0.53), bankroll=1000, theta=0.095)
    assert d.action == "pass" and "below" in d.reasons[0]


def test_bets_home_by_buying_the_short_side():
    # model ATL 0.62; ATL is short: ask = 1 - LAL bid = 0.53
    d = decide(_view(0.62), bankroll=1000, theta=0.095)
    assert d.action == "bet", d.reasons
    assert d.side_team == "ATL" and d.long_side is False
    assert d.quote.vwap == pytest.approx(0.53)
    assert d.quote.edge >= 0.04
    assert 0 < d.stake <= 1000


def test_bets_away_by_buying_the_long_side():
    d = decide(_view(0.40), bankroll=1000, theta=0.095)  # LAL 0.60 vs ask 0.48
    assert d.action == "bet" and d.side_team == "LAL" and d.long_side is True


def test_disagreement_cap_flags_review_instead_of_betting():
    d = decide(_view(0.75), bankroll=1000, theta=0.095)  # ATL 0.75 vs market 0.525
    assert d.action == "review"
    assert "cap" in d.reasons[0]


def test_uncertain_availability_requires_edge_in_both_scenarios():
    d = decide(_view(0.62, play=0.66, sit=0.54, uncertain=True), bankroll=1000, theta=0.095)
    assert d.action == "pass" and "sits" in d.reasons[0]
    d = decide(_view(0.62, play=0.64, sit=0.60, uncertain=True), bankroll=1000, theta=0.095)
    assert d.action == "bet"


def test_closed_market_and_thin_book_pass():
    assert (
        decide(
            _view(0.62, book=_book(state="MARKET_STATE_SUSPENDED")), bankroll=1000, theta=0.095
        ).action
        == "pass"
    )
    thin = decide(_view(0.62, book=_book(depth=50)), bankroll=1000, theta=0.095)
    assert thin.action == "pass" and "thin" in thin.reasons[0]


def test_stake_follows_quarter_kelly_on_shrunk_probability():
    d = decide(_view(0.62), bankroll=10_000, theta=0.095)
    # shrunk p = 0.5*0.62 + 0.5*0.525 = 0.5725; all-in ~0.53+0.0237+0.005=0.5587
    all_in = 0.53 + 0.095 * 0.53 * 0.47 + 0.005
    expected = 0.25 * (0.5725 - all_in) / (1 - all_in) * 10_000
    assert d.stake == pytest.approx(expected, rel=0.02)
