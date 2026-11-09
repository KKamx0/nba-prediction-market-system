"""NBA team reference data and name resolution.

Every source spells teams differently: nba_api uses ``GSW``, Polymarket uses
``gs``, odds archives use "Golden State" or "GoldenState", and older seasons use
defunct names (New Jersey Nets, Charlotte Bobcats, Seattle SuperSonics). All of
them resolve to one canonical three-letter code here, and unknown names raise
instead of being guessed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Team:
    abbr: str  # canonical, matches nba_api
    nba_id: int
    name: str  # current full name
    lat: float  # home arena
    lon: float
    tz: str  # IANA zone of the home arena


TEAMS: dict[str, Team] = {
    t.abbr: t
    for t in [
        Team("ATL", 1610612737, "Atlanta Hawks", 33.757, -84.396, "America/New_York"),
        Team("BOS", 1610612738, "Boston Celtics", 42.366, -71.062, "America/New_York"),
        Team("BKN", 1610612751, "Brooklyn Nets", 40.683, -73.976, "America/New_York"),
        Team("CHA", 1610612766, "Charlotte Hornets", 35.225, -80.839, "America/New_York"),
        Team("CHI", 1610612741, "Chicago Bulls", 41.881, -87.674, "America/Chicago"),
        Team("CLE", 1610612739, "Cleveland Cavaliers", 41.496, -81.688, "America/New_York"),
        Team("DAL", 1610612742, "Dallas Mavericks", 32.790, -96.810, "America/Chicago"),
        Team("DEN", 1610612743, "Denver Nuggets", 39.749, -105.008, "America/Denver"),
        Team("DET", 1610612765, "Detroit Pistons", 42.341, -83.055, "America/New_York"),
        Team("GSW", 1610612744, "Golden State Warriors", 37.768, -122.388, "America/Los_Angeles"),
        Team("HOU", 1610612745, "Houston Rockets", 29.751, -95.362, "America/Chicago"),
        Team("IND", 1610612754, "Indiana Pacers", 39.764, -86.155, "America/New_York"),
        Team("LAC", 1610612746, "LA Clippers", 33.945, -118.341, "America/Los_Angeles"),
        Team("LAL", 1610612747, "Los Angeles Lakers", 34.043, -118.267, "America/Los_Angeles"),
        Team("MEM", 1610612763, "Memphis Grizzlies", 35.138, -90.051, "America/Chicago"),
        Team("MIA", 1610612748, "Miami Heat", 25.781, -80.188, "America/New_York"),
        Team("MIL", 1610612749, "Milwaukee Bucks", 43.045, -87.917, "America/Chicago"),
        Team("MIN", 1610612750, "Minnesota Timberwolves", 44.979, -93.276, "America/Chicago"),
        Team("NOP", 1610612740, "New Orleans Pelicans", 29.949, -90.082, "America/Chicago"),
        Team("NYK", 1610612752, "New York Knicks", 40.751, -73.993, "America/New_York"),
        Team("OKC", 1610612760, "Oklahoma City Thunder", 35.463, -97.515, "America/Chicago"),
        Team("ORL", 1610612753, "Orlando Magic", 28.539, -81.384, "America/New_York"),
        Team("PHI", 1610612755, "Philadelphia 76ers", 39.901, -75.172, "America/New_York"),
        Team("PHX", 1610612756, "Phoenix Suns", 33.446, -112.071, "America/Phoenix"),
        Team("POR", 1610612757, "Portland Trail Blazers", 45.532, -122.667, "America/Los_Angeles"),
        Team("SAC", 1610612758, "Sacramento Kings", 38.580, -121.500, "America/Los_Angeles"),
        Team("SAS", 1610612759, "San Antonio Spurs", 29.427, -98.438, "America/Chicago"),
        Team("TOR", 1610612761, "Toronto Raptors", 43.643, -79.379, "America/Toronto"),
        Team("UTA", 1610612762, "Utah Jazz", 40.768, -111.901, "America/Denver"),
        Team("WAS", 1610612764, "Washington Wizards", 38.898, -77.021, "America/New_York"),
    ]
}

BY_NBA_ID: dict[int, Team] = {t.nba_id: t for t in TEAMS.values()}

# Extra spellings seen in Polymarket slugs, odds archives and older seasons.
# Keys are normalized with _norm(). Franchise moves map to today's franchise.
_EXTRA_ALIASES: dict[str, str] = {
    # Polymarket US abbreviations
    "sa": "SAS",
    "pho": "PHX",
    "no": "NOP",
    "gs": "GSW",
    "ny": "NYK",
    "uta": "UTA",
    "was": "WAS",
    # alternate codes
    "bk": "BKN",
    "brk": "BKN",
    "njn": "BKN",
    "nj": "BKN",
    "cho": "CHA",
    "noh": "NOP",
    "nok": "NOP",
    "nor": "NOP",
    "gsw": "GSW",
    "sas": "SAS",
    "phx": "PHX",
    "nyk": "NYK",
    "uth": "UTA",
    "wsh": "WAS",
    "sea": "OKC",
    # city or nickname spellings common in odds archives
    "laclippers": "LAC",
    "losangelesclippers": "LAC",
    "lalakers": "LAL",
    "goldenstate": "GSW",
    "newyork": "NYK",
    "neworleans": "NOP",
    "oklahomacity": "OKC",
    "sanantonio": "SAS",
    "newjersey": "BKN",
    "newjerseynets": "BKN",
    "charlottebobcats": "CHA",
    "neworleanshornets": "NOP",
    "neworleansoklahomacityhornets": "NOP",
    "seattlesupersonics": "OKC",
    "seattle": "OKC",
    "brooklyn": "BKN",
    "sixers": "PHI",
    "philadelphia76ers": "PHI",
    "blazers": "POR",
    "trailblazers": "POR",
    "wolves": "MIN",
    "cavs": "CLE",
    "mavs": "DAL",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for t in TEAMS.values():
        city_nick = t.name.split(" ")
        aliases[_norm(t.abbr)] = t.abbr
        aliases[_norm(t.name)] = t.abbr
        aliases[_norm(city_nick[-1])] = t.abbr  # nickname, e.g. "celtics"
        aliases[_norm(" ".join(city_nick[:-1]))] = t.abbr  # city, e.g. "boston"
    # "Trail Blazers" has a two-word nickname; "Los Angeles" is ambiguous.
    aliases.pop(_norm("Portland Trail"), None)
    aliases[_norm("Portland")] = "POR"
    aliases.pop(_norm("Los Angeles"), None)
    aliases.pop(_norm("LA"), None)
    aliases.update(_EXTRA_ALIASES)
    return aliases


ALIASES = _build_aliases()


class UnknownTeamError(ValueError):
    pass


def resolve(name: str) -> str:
    """Return the canonical abbreviation for any known spelling."""
    key = _norm(name)
    if key in ALIASES:
        return ALIASES[key]
    raise UnknownTeamError(f"unknown NBA team: {name!r}")


def team(abbr_or_name: str) -> Team:
    return TEAMS[resolve(abbr_or_name)]


def travel_km(from_abbr: str, to_abbr: str) -> float:
    """Great-circle distance between two home arenas in kilometres."""
    a, b = team(from_abbr), team(to_abbr)
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))
