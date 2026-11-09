"""Player-name normalization for joining sources.

The injury report writes "Jackson Jr., Jaren"; nba_api writes "Jaren Jackson
Jr."; news writes "Jaren Jackson". Matching goes through ``name_key``, which
strips accents, punctuation, case and generational suffixes.
"""

from __future__ import annotations

import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def last_first_to_full(name: str) -> str:
    """'Jackson Jr., Jaren' -> 'Jaren Jackson Jr.'; names without a comma pass through."""
    if "," not in name:
        return " ".join(name.split())
    last, first = (part.strip() for part in name.split(",", 1))
    return " ".join(f"{first} {last}".split())


def name_key(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z ]", "", ascii_name.lower().replace("-", " ")).split()
    return " ".join(t for t in tokens if t not in _SUFFIXES)
