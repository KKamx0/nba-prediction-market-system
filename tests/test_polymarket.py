import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from edgebot.db import Store
from edgebot.ingest.http import JsonClient
from edgebot.ingest.polymarket import (
    GATEWAY_URL,
    OrderBook,
    ParseError,
    PolymarketPublic,
    book_from_snapshot,
    parse_book,
    parse_moneyline,
    snapshot_to_store,
)

FIX = Path(__file__).parent / "fixtures"
EVENTS = json.loads((FIX / "polymarket_nba_events.json").read_text())
BOOK = json.loads((FIX / "polymarket_book.json").read_text())
NOW = datetime(2026, 11, 9, 21, 30, tzinfo=UTC)


def test_parse_moneyline_reads_home_away_and_long_side():
    ml = parse_moneyline(EVENTS["events"][0])
    assert (ml.home, ml.away, ml.long_team, ml.short_team) == ("ATL", "LAL", "LAL", "ATL")
    assert ml.market_slug == "aec-nba-lal-atl-2026-11-09"
    assert ml.game_date_et == "2026-11-09"
    assert ml.start_utc == datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
    assert ml.fee_coefficient == 0.095

    ml2 = parse_moneyline(EVENTS["events"][1])
    assert (ml2.home, ml2.away, ml2.long_team) == ("GSW", "NOP", "GSW")


def test_parse_moneyline_ignores_events_without_moneyline():
    ev = copy.deepcopy(EVENTS["events"][0])
    ev["markets"] = ev["markets"][1:]  # only the spread market left
    assert parse_moneyline(ev) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["marketSides"].pop(),
        lambda m: [s.__setitem__("long", True) for s in m["marketSides"]],
        lambda m: m["marketSides"][0]["team"].__setitem__("ordering", "home"),
        lambda m: m["marketSides"][0]["team"].__setitem__("abbreviation", "zzz"),
    ],
)
def test_parse_moneyline_rejects_ambiguous_markets(mutate):
    ev = copy.deepcopy(EVENTS["events"][0])
    mutate(ev["markets"][0])
    with pytest.raises(ValueError):  # ParseError or UnknownTeamError
        parse_moneyline(ev)


def test_parse_book_validates_and_orders():
    book = parse_book(BOOK, fetched_at=NOW)
    assert book.is_open
    assert [lv.price for lv in book.bids] == [0.47, 0.46]
    assert [lv.price for lv in book.asks] == [0.48, 0.49, 0.51]
    assert book.mid() == pytest.approx(0.475)
    assert book.last_trade == pytest.approx(0.48)


def test_short_side_asks_are_complements_of_long_bids():
    book = parse_book(BOOK, fetched_at=NOW)
    short = book.asks_for(long_side=False)
    assert [lv.price for lv in short] == pytest.approx([0.53, 0.54])
    assert [lv.qty for lv in short] == [1500.0, 800.0]
    assert book.asks_for(long_side=True) == book.asks


@pytest.mark.parametrize(
    "path,value",
    [
        (("bids", 0, "px", "value"), "0.5000"),  # crossed: bid >= ask
        (("offers", 0, "px", "value"), "0.9900"),  # asks out of order
        (("bids", 0, "px", "value"), "1.2000"),  # price out of range
        (("bids", 0, "qty"), "-5"),  # negative size
    ],
)
def test_bad_books_raise(path, value):
    bad = copy.deepcopy(BOOK)
    node = bad["marketData"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ParseError):
        parse_book(bad, fetched_at=NOW)


def test_naive_fetch_time_rejected():
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ValueError):
        OrderBook(market_slug="m", bids=[], asks=[], state="x", fetched_at=naive)


@respx.mock
def test_client_lists_open_moneylines_and_fetches_books():
    respx.get(f"{GATEWAY_URL}/v2/leagues/nba/events").mock(
        return_value=httpx.Response(200, json=EVENTS)
    )
    respx.get(f"{GATEWAY_URL}/v1/markets/aec-nba-lal-atl-2026-11-09/book").mock(
        return_value=httpx.Response(200, json=BOOK)
    )
    pm = PolymarketPublic(JsonClient(GATEWAY_URL, rate_per_s=1000))
    mls = pm.nba_moneylines()
    assert [m.market_slug for m in mls] == [
        "aec-nba-lal-atl-2026-11-09",
        "aec-nba-no-gs-2026-11-09",
    ]  # the closed event is skipped
    assert pm.book(mls[0].market_slug).asks[0].price == 0.48


def test_snapshot_roundtrip_through_store():
    store = Store(":memory:")
    ml = parse_moneyline(EVENTS["events"][0])
    book = parse_book(BOOK, fetched_at=NOW)
    snapshot_to_store(store, ml, book)
    row = store.latest_market_snapshot_as_of(ml.market_slug, NOW)
    assert row["long_team"] == "LAL" and row["home"] == "ATL"
    rebuilt = book_from_snapshot(row)
    assert rebuilt.bids == book.bids and rebuilt.asks == book.asks
    assert rebuilt.fetched_at == NOW
