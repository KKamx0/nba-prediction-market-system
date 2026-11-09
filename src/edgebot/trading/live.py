"""Live trading: LiveExecutor and the gated entry point.

``run_live`` refuses to start unless ``gate.evaluate_gate`` passes: the paper
track record from the database plus LIVE_TRADING=true, JURISDICTION_CLEARED=true,
--confirm-live, a live bankroll cap and Polymarket keys.

Every live order:
* is a LIMIT order, immediate-or-cancel, at the price the decision computed;
* carries ``manualOrderIndicator = MANUAL_ORDER_INDICATOR_AUTOMATIC``;
* is previewed first, and is not sent if the preview disagrees with the request;
* is long-side (YES) only unless LIVE_ALLOW_SHORT=true. The docs don't state
  whether a BUY_SHORT limit price is quoted on the NO scale or the YES scale.
  edgebot sends NO-scale prices (what it computes for the short side). Confirm
  that with a one-contract preview before enabling short-side orders.

Order placement is never retried automatically (the SDK doesn't retry
non-idempotent calls). If the outcome of a submit is unknown, the order is
logged as 'unknown' and its full cost counts against exposure until reconciled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from edgebot.timeutil import utc_now
from edgebot.trading.executor import ExecutionResult, Executor, OrderRequest, record_execution

log = logging.getLogger(__name__)

FILL_TYPES = {"EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL"}


class ExecutionParseError(ValueError):
    pass


def order_params(req: OrderRequest) -> dict[str, Any]:
    return {
        "marketSlug": req.market_slug,
        "intent": "ORDER_INTENT_BUY_LONG" if req.long_side else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": f"{req.limit_price:.2f}", "currency": "USD"},
        "quantity": req.qty,
        "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def parse_executions(executions: list[dict]) -> list[tuple[float, float, float]]:
    """(price, qty, fee) for each fill. Raises on anything that doesn't look right."""
    fills = []
    for e in executions or []:
        if e.get("type") not in FILL_TYPES:
            continue
        try:
            qty = float(e["lastShares"])
            price = float(e["lastPx"]["value"])
            fee = float((e.get("commissionNotionalCollected") or {}).get("value", 0) or 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionParseError(f"unreadable execution: {exc}") from exc
        if not (0 < price < 1 and qty > 0 and fee >= 0):
            raise ExecutionParseError(f"implausible fill: price={price} qty={qty} fee={fee}")
        fills.append((price, qty, fee))
    return fills


class LiveExecutor(Executor):
    mode = "live"

    def __init__(
        self,
        store,  # noqa: ANN001
        client,  # noqa: ANN001 - polymarket_us.PolymarketUS
        *,
        allow_short: bool = False,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store, self.client = store, client
        self.allow_short = allow_short
        self.clock = clock

    def submit(self, req: OrderRequest) -> ExecutionResult:
        now = self.clock()
        if not req.long_side and not self.allow_short:
            log.warning(
                "refusing short-side live order on %s (LIVE_ALLOW_SHORT is off)", req.market_slug
            )
            return record_execution(self.store, self.mode, req, "refused_short_side", [], now)
        params = order_params(req)

        preview = (self.client.orders.preview({"request": params}) or {}).get("order", {})
        mismatched = [
            k for k in ("marketSlug", "intent") if k in preview and preview[k] != params[k]
        ]
        if mismatched:
            log.error("preview disagrees on %s for %s; not sending", mismatched, req.market_slug)
            return record_execution(self.store, self.mode, req, "refused_preview_mismatch", [], now)

        try:
            resp = self.client.orders.create(params)
        except Exception as exc:  # noqa: BLE001 - outcome unknown; never retry blindly
            log.error("live order on %s: outcome unknown (%s)", req.market_slug, exc)
            return record_execution(self.store, self.mode, req, "unknown", [], now)
        try:
            fills = parse_executions(resp.get("executions", []))
        except ExecutionParseError as exc:
            log.error("live order %s filled but unreadable: %s", resp.get("id"), exc)
            return record_execution(self.store, self.mode, req, "unknown", [], now, resp.get("id"))
        filled = sum(q for _, q, _ in fills)
        status = (
            "unfilled" if filled == 0 else ("filled" if filled + 1e-9 >= req.qty else "partial")
        )
        return record_execution(self.store, self.mode, req, status, fills, now, resp.get("id"))

    def cancel_all(self) -> int:
        resp = self.client.orders.cancel_all() or {}
        return len(resp.get("canceledOrderIds", []))


def buying_power(client) -> float:  # noqa: ANN001
    balances = (client.account.balances() or {}).get("balances", [])
    usd = [b for b in balances if b.get("currency", "USD") == "USD"]
    return float(usd[0].get("buyingPower", 0.0)) if usd else 0.0


def run_live(args, settings, store, client=None) -> int:  # noqa: ANN001
    from edgebot.cli import KILL_FILE, build_runner
    from edgebot.gate import evaluate_gate
    from edgebot.trading.risk import RiskEngine, RiskLimits
    from edgebot.trading.state import load_risk_state, log_risk_event

    gate = evaluate_gate(
        store,
        settings,
        confirm_live=bool(getattr(args, "confirm_live", False)),
        now=utc_now(),
        paper_bankroll=settings.paper_bankroll,
    )
    print(gate.render())
    if not gate.passed:
        return 3

    if client is None:
        from polymarket_us import PolymarketUS

        client = PolymarketUS(
            key_id=settings.polymarket_key_id.get(), secret_key=settings.polymarket_secret_key.get()
        )
    bankroll = min(buying_power(client), settings.live_bankroll_cap)
    if bankroll <= 0:
        print("no buying power available under the live bankroll cap")
        return 3
    risk = RiskEngine(RiskLimits(), load_risk_state(store, bankroll, mode="live"), KILL_FILE)
    risk.state.bankroll = min(risk.state.bankroll, bankroll)  # never size above cap or account
    log_risk_event(
        store,
        "live_start",
        f"bankroll ${risk.state.bankroll:,.2f} (cap ${settings.live_bankroll_cap:,.2f})",
    )
    runner = build_runner(
        settings,
        store,
        executor=LiveExecutor(store, client, allow_short=settings.live_allow_short),
        risk=risk,
    )
    print(f"LIVE mode: bankroll ${risk.state.bankroll:,.2f}; `edgebot kill` to stop")
    if getattr(args, "once", False):
        runner.cycle()
        return 0
    runner.run(args.interval)
    return 0
