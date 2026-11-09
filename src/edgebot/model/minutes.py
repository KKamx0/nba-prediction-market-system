"""Per-player minutes projection for tonight, with injury redistribution.

Pipeline for one team:

1. **Roster**: players whose most recent game (as of the decision time) was
   for this team, within ``lookback_days``. Traded players follow their latest team.
2. **Base minutes**: a blend of last game, last-5 and last-10 averages and
   season average (``MinutesModel``), trimmed a little on back-to-backs.
   The blend weights can be fit on history with ``fit``.
3. **Availability**: injury status becomes a probability of playing.
   News extractions (``minutes_delta``) adjust base minutes; they never touch
   probabilities of winning.
4. **Redistribution**: minutes freed by absences go to the remaining players
   in proportion to their usual minutes, capped per player, so the team still
   sums to 240.

Scenarios: ``expected`` weights uncertain players by their probability,
``all_play`` / ``all_sit`` set every uncertain player in or out. The trading
layer only bets if the edge survives both, per the spec.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from edgebot.names import name_key

TEAM_MINUTES = 240.0
PLAYER_CAP = 42.0
B2B_FACTOR = 0.97

PLAY_PROB: dict[str | None, float] = {
    "Out": 0.0,
    "Doubtful": 0.15,
    "Questionable": 0.55,
    "Probable": 0.93,
    "Available": 1.0,
    None: 1.0,  # not on the report
}
UNCERTAIN = {"Doubtful", "Questionable", "Probable"}


@dataclass(frozen=True)
class MinutesFeatures:
    last: float
    avg5: float
    avg10: float
    season_avg: float
    std5: float
    games: int
    days_rest: int | None

    def vector(self) -> np.ndarray:
        return np.array([self.last, self.avg5, self.avg10, self.season_avg])


def features_from_history(
    games: list[tuple[date, float]], game_day: date
) -> MinutesFeatures | None:
    """``games``: (date, minutes) for this player's games *before* ``game_day``."""
    past = sorted(g for g in games if g[0] < game_day)
    if not past:
        return None
    mins = [m for _, m in past]
    last5, last10 = mins[-5:], mins[-10:]
    return MinutesFeatures(
        last=mins[-1],
        avg5=statistics.fmean(last5),
        avg10=statistics.fmean(last10),
        season_avg=statistics.fmean(mins),
        std5=statistics.pstdev(last5) if len(last5) > 1 else 0.0,
        games=len(mins),
        days_rest=(game_day - past[-1][0]).days,
    )


@dataclass
class MinutesModel:
    weights: tuple[float, float, float, float] = (0.30, 0.35, 0.20, 0.15)

    def base(self, f: MinutesFeatures, back_to_back: bool = False) -> float:
        m = float(np.dot(self.weights, f.vector()))
        if back_to_back:
            m *= B2B_FACTOR
        return float(np.clip(m, 0.0, PLAYER_CAP))

    @classmethod
    def fit(cls, samples: Iterable[tuple[MinutesFeatures, float]]) -> MinutesModel:
        """Least-squares weights on (features, actual minutes) pairs, kept non-negative."""
        pairs = list(samples)
        if len(pairs) < 200:
            return cls()
        X = np.array([f.vector() for f, _ in pairs])
        y = np.array([m for _, m in pairs])
        w, *_ = np.linalg.lstsq(X, y, rcond=None)
        w = np.clip(w, 0, None)
        if w.sum() == 0:
            return cls()
        return cls(weights=tuple(float(x) for x in w / w.sum()))  # type: ignore[arg-type]


def redistribute(
    base: Mapping[int, float],
    play_prob: Mapping[int, float],
    total: float = TEAM_MINUTES,
    cap: float = PLAYER_CAP,
) -> dict[int, float]:
    """Expected minutes per player, summing to ``total`` when the roster allows it.

    Players who might play absorb freed minutes in proportion to their base
    minutes (water-filling, so nobody exceeds ``cap``).
    """
    exp = {pid: base[pid] * play_prob.get(pid, 1.0) for pid in base}
    for _ in range(50):
        gap = total - sum(exp.values())
        open_ = [
            p for p in exp if play_prob.get(p, 1.0) > 0 and exp[p] < cap - 1e-9 and base[p] > 0
        ]
        if gap <= 1e-6 or not open_:
            break
        weight = sum(base[p] * play_prob.get(p, 1.0) for p in open_)
        if weight <= 0:
            break
        for p in open_:
            share = gap * base[p] * play_prob.get(p, 1.0) / weight
            exp[p] = min(cap, exp[p] + share)
    if sum(exp.values()) > total:  # healthy roster with inflated bases
        scale = total / sum(exp.values())
        exp = {p: m * scale for p, m in exp.items()}
    return exp


@dataclass
class TeamMinutes:
    team: str
    expected: dict[int, float]
    all_play: dict[int, float]
    all_sit: dict[int, float]
    full_strength: dict[int, float]
    names: dict[int, str] = field(default_factory=dict)
    statuses: dict[int, str] = field(default_factory=dict)
    unmatched_injuries: list[str] = field(default_factory=list)

    @property
    def has_uncertainty(self) -> bool:
        return any(s in UNCERTAIN for s in self.statuses.values())


def project_team(
    team: str,
    roster: Mapping[int, tuple[str, MinutesFeatures]],
    statuses: Mapping[str, str],
    *,
    model: MinutesModel | None = None,
    back_to_back: bool = False,
    minutes_delta: Mapping[str, float] | None = None,
) -> TeamMinutes:
    """``statuses`` and ``minutes_delta`` are keyed by player name (any spelling)."""
    model = model or MinutesModel()
    by_key = {name_key(name): pid for pid, (name, _) in roster.items()}
    status_by_pid: dict[int, str] = {}
    unmatched = []
    for name, status in statuses.items():
        pid = by_key.get(name_key(name))
        if pid is None:
            unmatched.append(name)
        else:
            status_by_pid[pid] = status
    deltas = {
        by_key[k]: v for n, v in (minutes_delta or {}).items() if (k := name_key(n)) in by_key
    }

    base = {
        pid: float(np.clip(model.base(f, back_to_back) + deltas.get(pid, 0.0), 0.0, PLAYER_CAP))
        for pid, (_, f) in roster.items()
    }
    prob = {pid: PLAY_PROB.get(status_by_pid.get(pid), 1.0) for pid in roster}
    certain = {pid: (1.0 if prob[pid] > 0 else 0.0) for pid in roster}
    play = {pid: (1.0 if status_by_pid.get(pid) in UNCERTAIN else certain[pid]) for pid in roster}
    sit = {pid: (0.0 if status_by_pid.get(pid) in UNCERTAIN else certain[pid]) for pid in roster}
    return TeamMinutes(
        team=team,
        expected=redistribute(base, prob),
        all_play=redistribute(base, play),
        all_sit=redistribute(base, sit),
        full_strength=redistribute(base, dict.fromkeys(roster, 1.0)),
        names={pid: name for pid, (name, _) in roster.items()},
        statuses=status_by_pid,
        unmatched_injuries=unmatched,
    )


def build_rosters(
    lines: Iterable[Mapping[str, Any]], game_day: date, lookback_days: int = 30
) -> dict[str, dict[int, tuple[str, MinutesFeatures]]]:
    """Group as-of player lines (with ``game_date``) into current rosters with features.

    Only games from the same season as ``game_day`` feed the features; a new
    season starts everyone fresh rather than inheriting last spring's role.
    """
    from edgebot.timeutil import season_for_date

    season = season_for_date(game_day)
    history: dict[int, list[tuple[date, float, str, str]]] = defaultdict(list)
    for r in lines:
        d = date.fromisoformat(r["game_date"])
        if d < game_day:
            history[int(r["player_id"])].append(
                (d, float(r["minutes"]), r["team"], r["player_name"])
            )
    rosters: dict[str, dict[int, tuple[str, MinutesFeatures]]] = defaultdict(dict)
    for pid, games in history.items():
        games.sort()
        last_day, _, team, name = games[-1]
        if (game_day - last_day).days > lookback_days:
            continue
        this_season = [(d, m) for d, m, t, _ in games if season_for_date(d) == season and t == team]
        f = features_from_history(this_season, game_day)
        if f is not None:
            rosters[team][pid] = (name, f)
    return rosters
