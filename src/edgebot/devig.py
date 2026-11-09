"""Odds conversion and vig removal.

A sportsbook's implied probabilities sum to more than 1 (the overround). To
use a line as a market prior we need fair probabilities that sum to exactly 1.

Two methods are provided:

* ``multiplicative``: scale both sides down proportionally. Simple, but it
  leaves the favorite-longshot bias in place.
* ``power`` (default): find k with p_home**k + p_away**k = 1. This takes more
  of the margin off the longshot, which matches how books actually shade lines.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable


def american_to_decimal(american: float) -> float:
    if not math.isfinite(american) or -100 < american < 100:
        raise ValueError(f"invalid American odds: {american}")
    return 1 + american / 100 if american > 0 else 1 + 100 / abs(american)


def decimal_to_implied(decimal_odds: float) -> float:
    if not math.isfinite(decimal_odds) or decimal_odds <= 1:
        raise ValueError(f"invalid decimal odds: {decimal_odds}")
    return 1 / decimal_odds


def overround(home_dec: float, away_dec: float) -> float:
    return decimal_to_implied(home_dec) + decimal_to_implied(away_dec) - 1


def devig_multiplicative(home_dec: float, away_dec: float) -> tuple[float, float]:
    ph, pa = decimal_to_implied(home_dec), decimal_to_implied(away_dec)
    total = ph + pa
    return ph / total, pa / total


def devig_power(home_dec: float, away_dec: float, tol: float = 1e-12) -> tuple[float, float]:
    ph, pa = decimal_to_implied(home_dec), decimal_to_implied(away_dec)
    total = ph + pa
    if abs(total - 1) < tol:
        return ph, pa
    # f(k) = ph^k + pa^k - 1 is strictly decreasing in k for 0 < p < 1.
    lo, hi = (1.0, 64.0) if total > 1 else (1e-6, 1.0)
    for _ in range(200):
        mid = (lo + hi) / 2
        if ph**mid + pa**mid > 1:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = (lo + hi) / 2
    fh, fa = ph**k, pa**k
    s = fh + fa
    return fh / s, fa / s


def fair_home_prob(home_dec: float, away_dec: float, method: str = "power") -> float:
    if method == "power":
        return devig_power(home_dec, away_dec)[0]
    if method == "multiplicative":
        return devig_multiplicative(home_dec, away_dec)[0]
    raise ValueError(f"unknown devig method: {method}")


def consensus_home_prob(
    quotes: Iterable[tuple[float, float]], method: str = "power", max_overround: float = 0.10
) -> float | None:
    """Median de-vigged home probability across books.

    Quotes with an implausible overround (stale or mis-keyed lines) are dropped.
    The median resists a single bad book better than the mean does.
    """
    probs = [
        fair_home_prob(h, a, method) for h, a in quotes if -0.01 <= overround(h, a) <= max_overround
    ]
    return statistics.median(probs) if probs else None
