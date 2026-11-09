"""Order execution behind one interface: PaperExecutor (default) and LiveExecutor.

PaperExecutor is our own simulator (Polymarket US has no sandbox). It fetches
the live public book at the moment of the order and fills by walking it at
our size, up to the limit price, immediate-or-cancel. The taker fee is charged
per fill with the exchange's rounding. Nothing is sent anywhere.

LiveExecutor lives in ``trading/live.py`` and refuses to start unless the
paper -> live gate passes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from edgebot.ingest.polymarket import OrderBook
from edgebot.timeutil import to_iso, utc_now
from edgebot.trading.edge import fee_cents, walk_book


@dataclass(frozen=True)
class OrderRequest:
    decision_id: int | None
    market_slug: str
    side_team: str
    long_side: bool
    qty: float
    limit_price: float
    game_date: str
    home: str
    away: str
    tip_utc: datetime | None = None


@dataclass
class ExecutionResult:
    order_id: int
    status: str  # 'filled' | 'partial' | 'unfilled'
    filled_qty: float = 0.0
    avg_price: float | None = None
    fees: float = 0.0
    fills: list[tuple[float, float, float]] = field(default_factory=list)  # price, qty, fee

    @property
    def cost(self) -> float:
        return sum(p * q for p, q, _ in self.fills)


class Executor(ABC):
    mode: str

    @abstractmethod
    def submit(self, req: OrderRequest) -> ExecutionResult: ...

    @abstractmethod
    def cancel_all(self) -> int: ...


def record_execution(
    store, mode: str, req: OrderRequest, result_status: str, fills, now, exchange_id=None
) -> ExecutionResult:  # noqa: E501
    """Write order, fills and (if anything filled) a position. Shared by both executors."""
    with store.conn:
        cur = store.conn.execute(
            """
            INSERT INTO orders (known_at, decision_id, mode, market_slug, side_team, long_side,
                                qty, limit_price, status, exchange_order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                to_iso(now),
                req.decision_id,
                mode,
                req.market_slug,
                req.side_team,
                int(req.long_side),
                req.qty,
                req.limit_price,
                result_status,
                exchange_id,
            ),
        )
        order_id = cur.lastrowid
        store.conn.executemany(
            "INSERT INTO fills (order_id, known_at, price, qty, fee) VALUES (?, ?, ?, ?, ?)",
            [(order_id, to_iso(now), p, q, f) for p, q, f in fills],
        )
    res = ExecutionResult(order_id=order_id, status=result_status, fills=list(fills))
    res.filled_qty = sum(q for _, q, _ in fills)
    res.fees = sum(f for _, _, f in fills)
    res.avg_price = res.cost / res.filled_qty if res.filled_qty else None
    if res.filled_qty > 0:
        with store.conn:
            store.conn.execute(
                """
                INSERT INTO positions (order_id, mode, market_slug, game_date, home, away,
                                       side_team, long_side, tip_utc, qty, cost, fees, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    mode,
                    req.market_slug,
                    req.game_date,
                    req.home,
                    req.away,
                    req.side_team,
                    int(req.long_side),
                    to_iso(req.tip_utc) if req.tip_utc else None,
                    res.filled_qty,
                    res.cost,
                    res.fees,
                    to_iso(now),
                ),
            )
    return res


class PaperExecutor(Executor):
    mode = "paper"

    def __init__(
        self,
        store,
        book_source: Callable[[str], OrderBook],
        theta: float,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.book_source = book_source
        self.theta = theta
        self.clock = clock

    def submit(self, req: OrderRequest) -> ExecutionResult:
        book = self.book_source(req.market_slug)  # fresh book at order time
        now = self.clock()
        if not book.is_open:
            return record_execution(self.store, self.mode, req, "unfilled", [], now)
        filled, _, levels = walk_book(book.asks_for(req.long_side), req.qty, req.limit_price)
        fills = [(p, q, fee_cents(p, q, self.theta)) for p, q in levels]
        status = (
            "unfilled" if filled == 0 else ("filled" if filled + 1e-9 >= req.qty else "partial")
        )
        return record_execution(self.store, self.mode, req, status, fills, now)

    def cancel_all(self) -> int:
        return 0  # paper orders are immediate-or-cancel; nothing ever rests
