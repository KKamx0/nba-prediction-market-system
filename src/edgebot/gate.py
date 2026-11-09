"""The paper -> live gate. LiveExecutor refuses to start unless every check passes.

Track-record checks are computed from the database with the same ``report``
code the daily report uses, restricted to the current season. Operator checks
are switches only a person can set. Nothing here can be satisfied by the model
or by the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from edgebot import report
from edgebot.config import Settings

MIN_PAPER_BETS = 300
MIN_WEEKS = 6
MAX_DRAWDOWN = 0.15


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateResult:
    checks: list[GateCheck]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def render(self) -> str:
        width = max(len(c.name) for c in self.checks)
        lines = [
            f"[{'pass' if c.passed else 'FAIL'}] {c.name:<{width}}  {c.detail}" for c in self.checks
        ]
        lines.append("live trading: ALLOWED" if self.passed else "live trading: BLOCKED")
        return "\n".join(lines)


def season_start(d: date) -> date:
    return date(d.year if d.month >= 8 else d.year - 1, 8, 1)


def evaluate_gate(
    store,  # noqa: ANN001
    settings: Settings,
    *,
    confirm_live: bool,
    now: datetime,
    paper_bankroll: float,
) -> GateResult:
    since = season_start(now.date()).isoformat()
    s = report.compute(store, mode="paper", starting_bankroll=paper_bankroll, since_date=since)
    checks: list[GateCheck] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(GateCheck(name, bool(ok), detail))

    add(
        "paper bets",
        s.n_settled >= MIN_PAPER_BETS,
        f"{s.n_settled} settled this season (need {MIN_PAPER_BETS})",
    )
    add(
        "weeks of paper trading",
        s.weeks_active >= MIN_WEEKS,
        f"{s.weeks_active:.1f} (need {MIN_WEEKS})",
    )
    if s.clv_ci is None:
        add("closing line value", False, "not enough positions with a closing price")
    else:
        lo, hi = s.clv_ci
        add(
            "closing line value",
            s.clv_mean is not None and s.clv_mean > 0 and lo > 0,
            f"mean {s.clv_mean:+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}] (must exclude zero)",
        )
    if s.model_log_loss is None:
        add("log loss vs market", False, "no scored predictions this season")
    else:
        add(
            "log loss vs market",
            s.model_log_loss < s.market_log_loss,
            f"model {s.model_log_loss:.4f} vs market {s.market_log_loss:.4f} (n={s.n_scored})",
        )
    add(
        "roi after fees",
        s.roi is not None and s.roi > 0,
        f"{s.roi:+.2%}" if s.roi is not None else "no settled bets",
    )
    add(
        "max drawdown",
        s.n_settled > 0 and s.max_drawdown < MAX_DRAWDOWN,
        f"{s.max_drawdown:.1%} (must be < {MAX_DRAWDOWN:.0%})",
    )

    add("LIVE_TRADING=true", settings.live_trading, "set" if settings.live_trading else "not set")
    add("--confirm-live", confirm_live, "given" if confirm_live else "flag missing")
    add(
        "live bankroll cap",
        settings.live_bankroll_cap > 0,
        (
            f"${settings.live_bankroll_cap:,.2f}"
            if settings.live_bankroll_cap > 0
            else "LIVE_BANKROLL_CAP not set"
        ),
    )
    keys = bool(settings.polymarket_key_id) and bool(settings.polymarket_secret_key)
    add(
        "polymarket api keys",
        keys,
        "present" if keys else "POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY missing",
    )
    return GateResult(checks)
