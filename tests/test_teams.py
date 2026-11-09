import pytest
from nba_api.stats.static import teams as nba_static_teams

from edgebot.teams import BY_NBA_ID, TEAMS, UnknownTeamError, resolve, travel_km


def test_thirty_teams_match_nba_api_ids_and_abbreviations():
    assert len(TEAMS) == 30
    for t in nba_static_teams.get_teams():
        ours = BY_NBA_ID[t["id"]]
        assert ours.abbr == t["abbreviation"]


@pytest.mark.parametrize(
    "spelling,abbr",
    [
        # Polymarket slug abbreviations
        ("gs", "GSW"),
        ("sa", "SAS"),
        ("pho", "PHX"),
        ("no", "NOP"),
        ("ny", "NYK"),
        ("bkn", "BKN"),
        # full names, cities, nicknames, punctuation
        ("Los Angeles Lakers", "LAL"),
        ("LA Clippers", "LAC"),
        ("L.A. Clippers", "LAC"),
        ("Golden State", "GSW"),
        ("GoldenState", "GSW"),
        ("Portland Trail Blazers", "POR"),
        ("Portland", "POR"),
        ("76ers", "PHI"),
        ("Celtics", "BOS"),
        # defunct names map to today's franchise
        ("New Jersey Nets", "BKN"),
        ("Charlotte Bobcats", "CHA"),
        ("New Orleans Hornets", "NOP"),
        ("Seattle SuperSonics", "OKC"),
    ],
)
def test_resolve_spellings(spelling, abbr):
    assert resolve(spelling) == abbr


@pytest.mark.parametrize("bad", ["Los Angeles", "LA", "Springfield Isotopes", ""])
def test_ambiguous_or_unknown_names_raise(bad):
    with pytest.raises(UnknownTeamError):
        resolve(bad)


def test_travel_distance_sane():
    assert travel_km("LAL", "LAC") < 30
    assert 3900 < travel_km("BOS", "LAL") < 4300
    assert travel_km("MIA", "MIA") == 0
