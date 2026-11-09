from datetime import UTC, datetime

import httpx
import pytest
import respx

from edgebot.ingest.http import JsonClient
from edgebot.ingest.odds_api import ODDS_API_URL, OddsApi, QuotaExhaustedError, parse_odds

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=UTC)


def _event(home="Atlanta Hawks", away="Los Angeles Lakers", books=None):
    return {
        "id": "abc",
        "commence_time": "2026-11-10T00:00:00Z",
        "home_team": home,
        "away_team": away,
        "bookmakers": (
            books
            if books is not None
            else [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Los Angeles Lakers", "price": 2.05},
                                {"name": "Atlanta Hawks", "price": 1.80},
                            ],
                        }
                    ],
                },
                {
                    "key": "fanduel",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Atlanta Hawks", "price": 1.83},
                                {"name": "Los Angeles Lakers", "price": 2.00},
                            ],
                        }
                    ],
                },
            ]
        ),
    }


def test_parse_maps_prices_to_home_and_away_regardless_of_order():
    rows = parse_odds([_event()], NOW)
    by_book = {r["bookmaker"]: r for r in rows}
    assert by_book["draftkings"]["home_dec"] == 1.80
    assert by_book["draftkings"]["away_dec"] == 2.05
    assert by_book["fanduel"]["home_dec"] == 1.83
    r = rows[0]
    # tip at 00:00 UTC on Nov 10 is 7pm ET on Nov 9
    assert (r["game_date"], r["home"], r["away"], r["kind"]) == ("2026-11-09", "ATL", "LAL", "live")
    assert r["known_at"] == NOW


def test_parse_skips_malformed_events_and_markets():
    bad_price = _event(
        books=[
            {
                "key": "x",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Atlanta Hawks", "price": 0.5},
                            {"name": "Los Angeles Lakers", "price": 2.0},
                        ],
                    }
                ],
            }
        ]
    )
    unknown_team = _event(home="Springfield Isotopes")
    wrong_names = _event(
        books=[
            {
                "key": "x",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Boston Celtics", "price": 1.9},
                            {"name": "Los Angeles Lakers", "price": 1.9},
                        ],
                    }
                ],
            }
        ]
    )
    assert parse_odds([bad_price, unknown_team, wrong_names], NOW) == []


@respx.mock
def test_client_tracks_quota_and_refuses_below_reserve():
    route = respx.get(f"{ODDS_API_URL}/v4/sports/basketball_nba/odds").mock(
        return_value=httpx.Response(200, json=[_event()], headers={"x-requests-remaining": "26"})
    )
    api = OddsApi(
        "secret-key", client=JsonClient(ODDS_API_URL, rate_per_s=1000), reserve_credits=25
    )
    assert len(api.nba_moneylines()) == 2
    assert api.remaining == 26
    assert route.calls[0].request.url.params["apiKey"] == "secret-key"
    route.mock(return_value=httpx.Response(200, json=[], headers={"x-requests-remaining": "25"}))
    api.nba_moneylines()
    with pytest.raises(QuotaExhaustedError):
        api.nba_moneylines()


def test_key_never_in_repr_and_required():
    assert "secret" not in repr(OddsApi("secret-key"))
    with pytest.raises(ValueError):
        OddsApi("")


@respx.mock
def test_http_errors_do_not_leak_the_key():
    respx.get(f"{ODDS_API_URL}/v4/sports/basketball_nba/odds").mock(
        return_value=httpx.Response(401)
    )
    api = OddsApi("secret-key", client=JsonClient(ODDS_API_URL, rate_per_s=1000))
    with pytest.raises(Exception) as exc:
        api.nba_moneylines()
    assert "secret-key" not in str(exc.value)
