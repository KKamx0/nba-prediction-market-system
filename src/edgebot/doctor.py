"""``edgebot doctor``: is this machine ready to run paper mode?

Each check is ok / warn / fail with a one-line fix. Only failures block.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from edgebot.config import Settings
from edgebot.db import MIGRATIONS
from edgebot.timeutil import season_for_date, utc_now


@dataclass(frozen=True)
class Check:
    name: str
    level: str  # 'ok' | 'warn' | 'fail'
    detail: str


def _count(store, sql: str, params=()) -> int:  # noqa: ANN001
    return store.query(sql, params)[0][0]


def data_checks(
    store, settings: Settings, models_dir: Path = Path("models")
) -> list[Check]:  # noqa: ANN001
    out = []
    v = store.schema_version()
    out.append(
        Check(
            "database", "ok" if v == len(MIGRATIONS) else "fail", f"{settings.db_path} schema v{v}"
        )
    )

    seasons = store.query(
        "SELECT g.season, COUNT(*) AS n FROM games g JOIN game_results r USING (game_id) "
        "GROUP BY g.season ORDER BY g.season"
    )
    if not seasons:
        out.append(
            Check("nba results", "fail", "none loaded: edgebot ingest nba --season 2025-26 ...")
        )
    else:
        desc = ", ".join(f"{r['season']} ({r['n']})" for r in seasons)
        level = "ok" if len(seasons) >= 3 else "warn"
        hint = "" if level == "ok" else "; load 3+ seasons for a validation + test split"
        out.append(Check("nba results", level, desc + hint))

    current = season_for_date(utc_now().date())
    n_lines = _count(
        store,
        "SELECT COUNT(*) FROM player_games pg JOIN games g USING (game_id) WHERE g.season = ?",
        (current,),
    )
    out.append(
        Check(
            "current season box scores",
            "ok" if n_lines else "warn",
            f"{n_lines} player lines for {current}"
            + ("" if n_lines else f": edgebot ingest nba --season {current}"),
        )
    )

    closing = store.query(
        "SELECT substr(game_date, 1, 4) AS y, COUNT(*) AS n FROM odds_quotes "
        "WHERE kind = 'closing' GROUP BY y ORDER BY y"
    )
    out.append(
        Check(
            "historical closing lines",
            "ok" if closing else "fail",
            ", ".join(f"{r['y']}: {r['n']}" for r in closing)
            or "none: see docs/data.md, edgebot ingest odds-history",
        )
    )

    model = models_dir / "game_model.pkl"
    choice = settings.db_path.parent / "model_choice.json"
    if not model.exists():
        out.append(Check("trained model", "fail", "missing: edgebot backtest ... && edgebot train"))
    else:
        name = json.loads(choice.read_text())["model"] if choice.exists() else "unknown"
        level, note = "ok", f"{name} ({model})"
        if name == "market":
            level = "warn"
            note += ": no feature model beat the market in the backtest; edges will be rare"
        out.append(Check("trained model", level, note))

    out.append(
        Check(
            "odds api key",
            "ok" if settings.odds_api_key else "fail",
            (
                "set"
                if settings.odds_api_key
                else "ODDS_API_KEY missing: the model needs a sportsbook prior"
            ),
        )
    )
    out.append(
        Check(
            "anthropic api key",
            "ok" if settings.anthropic_api_key else "warn",
            (
                "set (news extraction on)"
                if settings.anthropic_api_key
                else "not set: runs on the official injury report only"
            ),
        )
    )
    out.append(
        Check(
            "discord webhook",
            "ok" if settings.discord_webhook_url else "warn",
            "set" if settings.discord_webhook_url else "not set: no daily report delivery",
        )
    )
    kill = Path("run") / "KILL"
    out.append(
        Check(
            "kill switch", "warn" if kill.exists() else "ok", "ENGAGED" if kill.exists() else "off"
        )
    )
    states = store.query("SELECT state_json FROM risk_states")
    shutdown = any(json.loads(r["state_json"]).get("shutdown") for r in states)
    out.append(
        Check(
            "risk breakers",
            "fail" if shutdown else "ok",
            "drawdown shutdown: edgebot risk status / reset --confirm" if shutdown else "clear",
        )
    )
    return out


NETWORK_TARGETS = {
    "polymarket us gateway": "https://gateway.polymarket.us/v2/leagues/nba/events?limit=1",
    "nba stats": "https://stats.nba.com/stats/scoreboardv2?GameDate=2026-01-01&LeagueID=00&DayOffset=0",
    "nba injury report page": "https://official.nba.com/nba-injury-report-2026-27-season/",
    "the odds api": "https://api.the-odds-api.com/v4/sports",
}


def network_checks(probe: Callable[[str], int]) -> list[Check]:
    """``probe(url) -> HTTP status`` (or raises). 401 from the odds API just means no key sent."""
    out = []
    for name, url in NETWORK_TARGETS.items():
        try:
            status = probe(url)
        except Exception as exc:  # noqa: BLE001
            out.append(Check(name, "fail", f"unreachable: {type(exc).__name__}"))
            continue
        ok = status < 400 or (name == "the odds api" and status == 401)
        hint = "" if ok else " (datacenter IP? run from a home connection)"
        out.append(Check(name, "ok" if ok else "fail", f"HTTP {status}{hint}"))
    return out


def render(checks: list[Check]) -> str:
    mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    width = max(len(c.name) for c in checks)
    return "\n".join(f"[{mark[c.level]}] {c.name:<{width}}  {c.detail}" for c in checks)
