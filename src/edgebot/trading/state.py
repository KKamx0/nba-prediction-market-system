"""Persist RiskState in the database so limits survive restarts.

A drawdown shutdown in particular must not be undone by restarting the
process; only ``edgebot risk reset --confirm`` clears it.
"""

from __future__ import annotations

import json
from datetime import datetime

from edgebot.timeutil import parse_iso, to_iso, utc_now
from edgebot.trading.risk import RiskState


def load_risk_state(store, initial_bankroll: float) -> RiskState:  # noqa: ANN001
    rows = store.query("SELECT state_json FROM risk_state WHERE id = 1")
    if not rows:
        return RiskState(
            bankroll=initial_bankroll, peak=initial_bankroll, day_start=initial_bankroll
        )
    d = json.loads(rows[0]["state_json"])
    return RiskState(
        bankroll=d["bankroll"],
        peak=d["peak"],
        day_start=d["day_start"],
        open_exposure=d["open_exposure"],
        games_with_positions=set(d["games_with_positions"]),
        markets_traded=set(d["markets_traded"]),
        halted_until=parse_iso(d["halted_until"]) if d["halted_until"] else None,
        shutdown=d["shutdown"],
        shutdown_reason=d["shutdown_reason"],
    )


def save_risk_state(store, state: RiskState, now: datetime | None = None) -> None:  # noqa: ANN001
    d = {
        "bankroll": state.bankroll,
        "peak": state.peak,
        "day_start": state.day_start,
        "open_exposure": state.open_exposure,
        "games_with_positions": sorted(state.games_with_positions),
        "markets_traded": sorted(state.markets_traded),
        "halted_until": to_iso(state.halted_until) if state.halted_until else None,
        "shutdown": state.shutdown,
        "shutdown_reason": state.shutdown_reason,
    }
    with store.conn:
        store.conn.execute(
            """
            INSERT INTO risk_state (id, state_json, updated_at) VALUES (1, ?, ?)
            ON CONFLICT (id) DO UPDATE SET state_json = excluded.state_json,
                                           updated_at = excluded.updated_at
            """,
            (json.dumps(d), to_iso(now or utc_now())),
        )


def log_risk_event(
    store, kind: str, detail: str, now: datetime | None = None
) -> None:  # noqa: ANN001
    with store.conn:
        store.conn.execute(
            "INSERT INTO risk_events (known_at, kind, detail) VALUES (?, ?, ?)",
            (to_iso(now or utc_now()), kind, detail),
        )
