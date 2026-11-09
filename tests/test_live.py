from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from edgebot.config import Secret, Settings
from edgebot.trading.executor import OrderRequest
from edgebot.trading.live import (
    LiveExecutor,
    buying_power,
    order_params,
    parse_executions,
    run_live,
)

NOW = datetime(2027, 1, 20, 23, 0, tzinfo=UTC)


def _req(**kw):
    base = dict(
        decision_id=None, market_slug="aec-nba-lal-bos-2027-01-20", side_team="LAL", long_side=True,
        qty=20.0, limit_price=0.48, game_date="2027-01-20", home="BOS", away="LAL",
    )  # fmt: skip
    return OrderRequest(**{**base, **kw})


def _fill(qty, px, fee, kind="EXECUTION_TYPE_FILL"):
    return {
        "type": kind,
        "lastShares": str(qty),
        "lastPx": {"value": str(px), "currency": "USD"},
        "commissionNotionalCollected": {"value": str(fee), "currency": "USD"},
    }


class FakeOrders:
    def __init__(self, create_response=None, preview=None, raise_on_create=None):
        self.created, self.previewed = [], []
        self.create_response = create_response or {
            "id": "ord-1",
            "executions": [_fill(20, 0.48, 0.23)],
        }
        self.preview_response = preview
        self.raise_on_create = raise_on_create

    def preview(self, params):
        self.previewed.append(params)
        return self.preview_response or {"order": dict(params["request"])}

    def create(self, params):
        self.created.append(params)
        if self.raise_on_create:
            raise self.raise_on_create
        return self.create_response

    def cancel_all(self, params=None):
        return {"canceledOrderIds": ["a", "b"]}


def _client(**kw):
    return SimpleNamespace(
        orders=FakeOrders(**kw),
        account=SimpleNamespace(
            balances=lambda: {"balances": [{"currency": "USD", "buyingPower": 812.5}]}
        ),
    )


def test_order_params_are_limit_ioc_and_flagged_automatic():
    p = order_params(_req())
    assert p == {
        "marketSlug": "aec-nba-lal-bos-2027-01-20",
        "intent": "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": "0.48", "currency": "USD"},
        "quantity": 20.0,
        "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }
    assert order_params(_req(long_side=False))["intent"] == "ORDER_INTENT_BUY_SHORT"


def test_live_fill_is_previewed_then_recorded(store):
    client = _client()
    r = LiveExecutor(store, client, clock=lambda: NOW).submit(_req())
    assert len(client.orders.previewed) == 1 and len(client.orders.created) == 1
    assert r.status == "filled" and r.filled_qty == 20 and r.fees == pytest.approx(0.23)
    (pos,) = store.query("SELECT * FROM positions")
    assert pos["mode"] == "live" and pos["cost"] == pytest.approx(9.6)
    assert store.query("SELECT exchange_order_id FROM orders")[0][0] == "ord-1"


def test_partial_and_unfilled_ioc(store):
    partial = _client(create_response={"id": "o", "executions": [_fill(5, 0.48, 0.06, "EXECUTION_TYPE_PARTIAL_FILL"),
                                                                 {"type": "EXECUTION_TYPE_CANCELED"}]})  # fmt: skip
    assert LiveExecutor(store, partial, clock=lambda: NOW).submit(_req()).status == "partial"
    none = _client(
        create_response={"id": "o2", "executions": [{"type": "EXECUTION_TYPE_CANCELED"}]}
    )
    assert LiveExecutor(store, none, clock=lambda: NOW).submit(_req()).status == "unfilled"


def test_short_side_refused_unless_enabled(store):
    client = _client()
    r = LiveExecutor(store, client, clock=lambda: NOW).submit(_req(long_side=False))
    assert r.status == "refused_short_side" and client.orders.created == []
    allowed = LiveExecutor(store, _client(), allow_short=True, clock=lambda: NOW).submit(
        _req(long_side=False)
    )
    assert allowed.status == "filled"


def test_preview_mismatch_blocks_the_order(store):
    client = _client(
        preview={"order": {"marketSlug": "some-other-market", "intent": "ORDER_INTENT_BUY_LONG"}}
    )
    r = LiveExecutor(store, client, clock=lambda: NOW).submit(_req())
    assert r.status == "refused_preview_mismatch" and client.orders.created == []


def test_unknown_outcome_is_recorded_not_retried(store):
    client = _client(raise_on_create=TimeoutError("read timed out"))
    r = LiveExecutor(store, client, clock=lambda: NOW).submit(_req())
    assert r.status == "unknown" and len(client.orders.created) == 1
    assert store.query("SELECT status FROM orders")[0][0] == "unknown"


def test_unreadable_fills_are_unknown_not_guessed(store):
    bad = _client(create_response={"id": "o", "executions": [_fill(20, 1.7, 0.2)]})
    assert LiveExecutor(store, bad, clock=lambda: NOW).submit(_req()).status == "unknown"
    with pytest.raises(ValueError):
        parse_executions([{"type": "EXECUTION_TYPE_FILL", "lastShares": "x"}])


def test_cancel_all_and_buying_power():
    client = _client()
    assert LiveExecutor(None, client).cancel_all() == 2
    assert buying_power(client) == 812.5
    assert (
        buying_power(SimpleNamespace(account=SimpleNamespace(balances=lambda: {"balances": []})))
        == 0
    )


def test_run_live_refuses_when_gate_fails(store, capsys):
    settings = Settings(live_trading=True, jurisdiction_cleared=True, live_bankroll_cap=100,
                        polymarket_key_id=Secret("k"), polymarket_secret_key=Secret("s"))  # fmt: skip
    client = _client()
    rc = run_live(
        SimpleNamespace(confirm_live=True, once=True, interval=300), settings, store, client
    )
    assert rc == 3
    assert "BLOCKED" in capsys.readouterr().out
    assert client.orders.created == []
