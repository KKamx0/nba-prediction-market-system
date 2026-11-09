"""The Odds API (free tier): live NBA moneylines from US books.

Used as the benchmark "sharp" market prior going forward. The free tier gives
500 credits a month, and one h2h call for one region costs one credit, so the
client refuses to fetch once the remaining quota drops below a reserve.

Quotes are stamped with our fetch time, not the book's ``last_update``: we
can only claim to have known a price from the moment we actually saw it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from edgebot.ingest.http import JsonClient
from edgebot.teams import UnknownTeamError, resolve
from edgebot.timeutil import ET, parse_iso, utc_now

log = logging.getLogger(__name__)

ODDS_API_URL = "https://api.the-odds-api.com"
SPORT = "basketball_nba"


class QuotaExhaustedError(RuntimeError):
    pass


class _Outcome(BaseModel):
    name: str
    price: float = Field(gt=1)


class _Market(BaseModel):
    key: str
    outcomes: list[_Outcome]


class _Book(BaseModel):
    key: str
    markets: list[_Market]


class _Event(BaseModel):
    id: str
    commence_time: str
    home_team: str
    away_team: str
    bookmakers: list[_Book] = []


def parse_odds(payload: list[dict[str, Any]], fetched_at: datetime) -> list[dict[str, Any]]:
    """Flatten the API response into odds_quotes rows. Bad events are skipped."""
    rows = []
    for raw in payload:
        try:
            ev = _Event.model_validate(raw)
            home, away = resolve(ev.home_team), resolve(ev.away_team)
        except (ValueError, UnknownTeamError) as exc:
            log.warning("skipping malformed odds event: %s", exc)
            continue
        game_date = parse_iso(ev.commence_time).astimezone(ET).date().isoformat()
        for book in ev.bookmakers:
            for market in book.markets:
                if market.key != "h2h" or len(market.outcomes) != 2:
                    continue
                prices = {}
                for o in market.outcomes:
                    try:
                        prices[resolve(o.name)] = o.price
                    except UnknownTeamError:
                        break
                if set(prices) != {home, away}:
                    continue
                rows.append(
                    {
                        "known_at": fetched_at,
                        "source": "odds_api",
                        "bookmaker": book.key,
                        "kind": "live",
                        "game_date": game_date,
                        "home": home,
                        "away": away,
                        "home_dec": prices[home],
                        "away_dec": prices[away],
                    }
                )
    return rows


class OddsApi:
    def __init__(
        self,
        api_key: str,
        *,
        client: JsonClient | None = None,
        reserve_credits: int = 25,
        regions: str = "us",
    ) -> None:
        if not api_key:
            raise ValueError("ODDS_API_KEY is not set")
        self._key = api_key
        self._client = client or JsonClient(ODDS_API_URL, rate_per_s=1.0)
        self._reserve = reserve_credits
        self._regions = regions
        self.remaining: int | None = None

    def __repr__(self) -> str:  # never expose the key
        return f"OddsApi(remaining={self.remaining})"

    def nba_moneylines(self) -> list[dict[str, Any]]:
        if self.remaining is not None and self.remaining <= self._reserve:
            raise QuotaExhaustedError(f"only {self.remaining} credits left; keeping reserve")
        fetched_at = utc_now()
        payload = self._client.get(
            f"/v4/sports/{SPORT}/odds",
            params={
                "apiKey": self._key,
                "regions": self._regions,
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
        )
        remaining = self._client.last_headers.get("x-requests-remaining")
        if remaining is not None:
            self.remaining = int(float(remaining))
        return parse_odds(payload, fetched_at)
