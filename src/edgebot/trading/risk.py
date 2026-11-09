"""Hard risk limits, enforced in code.

Every proposed bet goes through ``RiskEngine.check``, which returns an
allowed/blocked decision, a possibly reduced stake, and human-readable reasons.
The engine never increases a stake.

State changes go through ``record_fill`` / ``record_pnl`` / ``new_day``.
A drawdown shutdown survives restarts (the runner persists ``RiskState``)
and only ``manual_reset(confirm=True)`` clears it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

from edgebot.timeutil import ET


@dataclass(frozen=True)
class RiskLimits:
    max_position_frac: float = 0.02
    max_nightly_exposure_frac: float = 0.10
    daily_loss_limit_frac: float = 0.05
    max_drawdown_frac: float = 0.20
    min_stake: float = 1.0  # dollars; smaller orders aren't worth the fee rounding


@dataclass
class RiskState:
    bankroll: float
    peak: float
    day_start: float
    open_exposure: float = 0.0
    games_with_positions: set[str] = field(default_factory=set)
    markets_traded: set[str] = field(default_factory=set)
    halted_until: datetime | None = None
    shutdown: bool = False
    shutdown_reason: str = ""


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    stake: float
    reasons: list[str]


def _next_et_midnight(now: datetime) -> datetime:
    local = now.astimezone(ET)
    return datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=ET)


class RiskEngine:
    def __init__(self, limits: RiskLimits, state: RiskState, kill_file: str | Path) -> None:
        self.limits = limits
        self.state = state
        self.kill_file = Path(kill_file)

    # -- status --------------------------------------------------------------
    def killed(self) -> bool:
        return self.kill_file.exists()

    def drawdown(self) -> float:
        return 0.0 if self.state.peak <= 0 else 1 - self.state.bankroll / self.state.peak

    def daily_loss(self) -> float:
        s = self.state
        return 0.0 if s.day_start <= 0 else max(0.0, (s.day_start - s.bankroll) / s.day_start)

    def _update_breakers(self, now: datetime) -> None:
        s, lim = self.state, self.limits
        if self.drawdown() >= lim.max_drawdown_frac - 1e-12 and not s.shutdown:
            s.shutdown = True
            s.shutdown_reason = f"drawdown {self.drawdown():.1%} from peak {s.peak:.2f}"
        if self.daily_loss() >= lim.daily_loss_limit_frac - 1e-12 and s.halted_until is None:
            s.halted_until = _next_et_midnight(now)

    # -- the gate ------------------------------------------------------------
    def check(
        self, *, game_key: str, market_slug: str, stake: float, now: datetime
    ) -> RiskDecision:
        s, lim = self.state, self.limits
        self._update_breakers(now)

        def no(reason: str) -> RiskDecision:
            return RiskDecision(False, 0.0, [reason])

        if self.killed():
            return no(f"kill switch engaged ({self.kill_file})")
        if s.shutdown:
            return no(f"shutdown: {s.shutdown_reason}; manual reset required")
        if s.halted_until is not None and now < s.halted_until:
            return no(
                f"daily loss limit hit ({self.daily_loss():.1%}); halted until {s.halted_until}"
            )
        if game_key in s.games_with_positions:
            return no("already have a position on this game (max one correlated position)")
        if market_slug in s.markets_traded:
            return no("market already traded today (no averaging down or chasing)")
        if stake <= 0:
            return no("non-positive stake")

        reasons = []
        allowed = stake
        pos_cap = lim.max_position_frac * s.bankroll
        if allowed > pos_cap:
            allowed = pos_cap
            reasons.append(f"capped at {lim.max_position_frac:.0%} of bankroll")
        room = lim.max_nightly_exposure_frac * s.bankroll - s.open_exposure
        if allowed > room:
            allowed = max(0.0, room)
            reasons.append(f"capped by {lim.max_nightly_exposure_frac:.0%} nightly exposure")
        if allowed < lim.min_stake:
            return no(f"stake {allowed:.2f} below minimum {lim.min_stake:.2f}")
        return RiskDecision(True, allowed, reasons or ["within limits"])

    # -- state updates -----------------------------------------------------
    def record_fill(self, *, game_key: str, market_slug: str, cost: float) -> None:
        self.state.open_exposure += cost
        self.state.games_with_positions.add(game_key)
        self.state.markets_traded.add(market_slug)

    def record_settlement(self, *, cost: float, pnl: float, now: datetime) -> None:
        self.state.open_exposure = max(0.0, self.state.open_exposure - cost)
        self.record_pnl(pnl, now=now)

    def record_pnl(self, pnl: float, *, now: datetime) -> None:
        s = self.state
        s.bankroll += pnl
        s.peak = max(s.peak, s.bankroll)
        self._update_breakers(now)

    def new_day(self, *, now: datetime) -> None:
        """Start of an Eastern trading day: reset the daily baseline and per-day sets."""
        s = self.state
        if s.halted_until is not None and now >= s.halted_until:
            s.halted_until = None
        s.day_start = s.bankroll
        # game keys and market slugs are date-specific, so clearing these can't
        # reopen last night's games; open exposure stays until positions settle
        s.markets_traded.clear()
        s.games_with_positions.clear()

    def manual_reset(self, *, confirm: bool) -> None:
        if not confirm:
            raise PermissionError("manual reset requires explicit confirmation")
        s = self.state
        s.shutdown, s.shutdown_reason = False, ""
        s.peak = s.bankroll  # measure drawdown from here on
        s.halted_until = None
