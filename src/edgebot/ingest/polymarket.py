"""Polymarket US public gateway: NBA moneyline discovery and order books.

Only the public gateway (no API key) is used here. Every payload is validated
before it reaches the database; a malformed book raises instead of being
stored, because a bad book would produce a fake edge.

Contract mechanics, from docs.polymarket.us: each moneyline market has a
"long" side (YES = that team wins) and a short side. The book is quoted for
the long side. Buying the short side at price q is equivalent to selling the
long side at 1 - q, so the short side's asks are the complements of the long
side's bids.

Terms note: Polymarket data is used for decisions and our own trade log only.
It is never used to train a model and never redistributed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from edgebot.ingest.http import JsonClient
from edgebot.teams import resolve
from edgebot.timeutil import parse_iso, utc_now

GATEWAY_URL = "https://gateway.polymarket.us"
MONEYLINE_TYPE = "SPORTS_MARKET_TYPE_MONEYLINE"


class Level(BaseModel):
    price: float = Field(gt=0, lt=1)
    qty: float = Field(gt=0)


class OrderBook(BaseModel):
    """Long-side book. Bids are best (highest) first, asks best (lowest) first."""

    market_slug: str
    bids: list[Level]
    asks: list[Level]
    state: str
    last_trade: float | None = None
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        return v

    @model_validator(mode="after")
    def _sorted_and_uncrossed(self) -> OrderBook:
        bp = [lv.price for lv in self.bids]
        ap = [lv.price for lv in self.asks]
        if bp != sorted(bp, reverse=True):
            raise ValueError("bids not sorted best-first")
        if ap != sorted(ap):
            raise ValueError("asks not sorted best-first")
        if bp and ap and bp[0] >= ap[0]:
            raise ValueError(f"crossed book: bid {bp[0]} >= ask {ap[0]}")
        return self

    @property
    def is_open(self) -> bool:
        return self.state == "MARKET_STATE_OPEN"

    def asks_for(self, long_side: bool) -> list[Level]:
        """Levels we can buy into for one side, best price first."""
        if long_side:
            return list(self.asks)
        return [Level(price=round(1 - lv.price, 6), qty=lv.qty) for lv in self.bids]

    def mid(self) -> float | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price + self.asks[0].price) / 2


@dataclass(frozen=True)
class Moneyline:
    event_slug: str
    market_slug: str
    start_utc: datetime
    home: str
    away: str
    long_team: str  # this team winning pays the long (YES) side
    fee_coefficient: float | None
    min_tick: float
    raw_title: str = field(default="", compare=False)

    @property
    def short_team(self) -> str:
        return self.away if self.long_team == self.home else self.home

    @property
    def game_date_et(self) -> str:
        """Event slugs carry the Eastern-time date, e.g. nba-lal-atl-2026-11-09."""
        return self.event_slug[-10:]


class ParseError(ValueError):
    pass


def _amount(obj: dict[str, Any] | None) -> float | None:
    return None if not obj else float(obj["value"])


def parse_book(payload: dict[str, Any], *, fetched_at: datetime | None = None) -> OrderBook:
    md = payload.get("marketData", payload)
    try:
        return OrderBook(
            market_slug=md["marketSlug"],
            bids=[
                Level(price=float(lv["px"]["value"]), qty=float(lv["qty"]))
                for lv in md.get("bids", [])
            ],
            asks=[
                Level(price=float(lv["px"]["value"]), qty=float(lv["qty"]))
                for lv in md.get("offers", [])
            ],
            state=md.get("state", "UNKNOWN"),
            last_trade=_amount((md.get("stats") or {}).get("lastTradePx")),
            fetched_at=fetched_at or utc_now(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"bad order book payload: {exc}") from exc


def parse_moneyline(event: dict[str, Any]) -> Moneyline | None:
    """Extract the full-game moneyline market from an NBA event, if present."""
    markets = [m for m in event.get("markets", []) if m.get("sportsMarketTypeV2") == MONEYLINE_TYPE]
    if not markets:
        return None
    if len(markets) > 1:
        raise ParseError(f"{event.get('slug')}: {len(markets)} moneyline markets")
    m = markets[0]
    sides = [s for s in m.get("marketSides", []) if s.get("team")]
    if len(sides) != 2:
        raise ParseError(f"{m.get('slug')}: expected 2 team sides, got {len(sides)}")
    by_order = {s["team"].get("ordering"): s for s in sides}
    if set(by_order) != {"home", "away"}:
        raise ParseError(f"{m.get('slug')}: sides lack home/away ordering")
    longs = [s for s in sides if s.get("long") is True]
    if len(longs) != 1:
        raise ParseError(f"{m.get('slug')}: expected exactly one long side")
    start = event.get("startTime") or m.get("gameStartTime")
    if not start:
        raise ParseError(f"{m.get('slug')}: missing start time")
    return Moneyline(
        event_slug=event["slug"],
        market_slug=m["slug"],
        start_utc=parse_iso(start),
        home=resolve(by_order["home"]["team"]["abbreviation"]),
        away=resolve(by_order["away"]["team"]["abbreviation"]),
        long_team=resolve(longs[0]["team"]["abbreviation"]),
        fee_coefficient=m.get("feeCoefficient"),
        min_tick=float(m.get("orderPriceMinTickSize") or 0.01),
        raw_title=event.get("title", ""),
    )


class PolymarketPublic:
    """Read-only client for gateway.polymarket.us."""

    def __init__(self, client: JsonClient | None = None) -> None:
        # 2 req/s keeps us an order of magnitude under the documented 25/s.
        self._client = client or JsonClient(GATEWAY_URL, rate_per_s=2.0)

    def nba_moneylines(self, limit: int = 100) -> list[Moneyline]:
        payload = self._client.get("/v2/leagues/nba/events", params={"limit": limit})
        out = []
        for event in payload.get("events", []):
            if event.get("closed") or not event.get("active", True):
                continue
            ml = parse_moneyline(event)
            if ml is not None:
                out.append(ml)
        return out

    def book(self, market_slug: str) -> OrderBook:
        return parse_book(self._client.get(f"/v1/markets/{market_slug}/book"))


def snapshot_to_store(store, ml: Moneyline, book: OrderBook) -> None:  # noqa: ANN001
    store.insert_market_snapshot(
        known_at=book.fetched_at,
        market_slug=ml.market_slug,
        event_slug=ml.event_slug,
        game_date=ml.game_date_et,
        home=ml.home,
        away=ml.away,
        long_team=ml.long_team,
        state=book.state,
        last_trade=book.last_trade,
        bids=[(lv.price, lv.qty) for lv in book.bids],
        asks=[(lv.price, lv.qty) for lv in book.asks],
    )


def book_from_snapshot(row) -> OrderBook:  # noqa: ANN001
    """Rebuild a validated OrderBook from a stored market_snapshots row."""
    return OrderBook(
        market_slug=row["market_slug"],
        bids=[Level(price=p, qty=q) for p, q in json.loads(row["bids_json"])],
        asks=[Level(price=p, qty=q) for p, q in json.loads(row["asks_json"])],
        state=row["state"] or "UNKNOWN",
        last_trade=row["last_trade"],
        fetched_at=parse_iso(row["known_at"]),
    )
