"""The trading loop. One ``cycle()`` per interval; ``run()`` repeats until killed.

Each cycle:

1. Kill switch set -> cancel all open orders and stop.
2. New Eastern day -> reset daily risk counters. Settle finished games.
3. Ingest new injury reports; snapshot sportsbook odds when due.
4. Snapshot every NBA moneyline book within the horizon (these snapshots
   are also what closing line value is measured against).
5. For games inside the decision window, re-evaluate when first seen, after
   any new injury report, or every ``reevaluate_every``: features as of now,
   model probabilities for the expected / plays / sits scenarios, decision,
   risk check, execution. Every decision is logged, including passes.
6. Persist risk state.

Network failures in any ingestion step are logged and the cycle continues.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import pandas as pd

from edgebot.ingest.polymarket import Moneyline, OrderBook, snapshot_to_store
from edgebot.model.features import FeatureBuilder, GameFeatures
from edgebot.timeutil import ET, to_iso, utc_now
from edgebot.trading.decision import Decision, GameView, decide
from edgebot.trading.edge import EdgeConfig, quote_side
from edgebot.trading.executor import Executor, OrderRequest
from edgebot.trading.risk import RiskEngine
from edgebot.trading.settlement import settle_positions
from edgebot.trading.sizing import SizingConfig
from edgebot.trading.state import log_risk_event, save_risk_state

log = logging.getLogger(__name__)


class KillSwitchError(RuntimeError):
    pass


@dataclass
class RunnerConfig:
    window_open: timedelta = timedelta(hours=6)  # start evaluating this long before tip
    window_close: timedelta = timedelta(minutes=10)  # stop this close to tip
    snapshot_horizon: timedelta = timedelta(hours=36)
    reevaluate_every: timedelta = timedelta(minutes=30)
    odds_every: timedelta = timedelta(minutes=60)
    default_theta: float = 0.095
    edge: EdgeConfig = field(default_factory=EdgeConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)


def predict_scenarios(model, f: GameFeatures) -> tuple[float, float, float]:
    """Model probability for expected availability, everyone-plays, everyone-sits."""
    rows = []
    for diff in (f.strength_diff, f.strength_diff_play, f.strength_diff_sit):
        g = replace(f, strength_diff=diff, injury_drag_diff=f.full_strength_diff - diff)
        rows.append(g.model_vector())
    p = model.predict(pd.DataFrame(rows))
    return float(p[0]), float(p[1]), float(p[2])


def game_key(game_date: str, home: str, away: str) -> str:
    return f"{game_date}:{away}@{home}"


@dataclass
class CycleReport:
    snapshots: int = 0
    evaluated: int = 0
    bets: int = 0
    settled: int = 0
    errors: list[str] = field(default_factory=list)


class Runner:
    def __init__(
        self,
        *,
        store,
        model,
        model_name: str,
        executor: Executor,
        risk: RiskEngine,
        polymarket,
        injuries: Callable[[], int] | None = None,
        odds: Callable[[], int] | None = None,
        results: Callable[[], object] | None = None,
        daily_report: Callable[[], object] | None = None,
        news: Callable[[set[str], dict[str, set[str]]], int] | None = None,
        config: RunnerConfig | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store, self.model, self.model_name = store, model, model_name
        self.executor, self.risk, self.pm = executor, risk, polymarket
        self.injuries, self.odds, self.results = injuries, odds, results
        self._results_day = None
        self.daily_report = daily_report
        self.news = news
        self._report_day = None
        self.cfg = config or RunnerConfig()
        self.clock = clock
        self.builder = FeatureBuilder(store)
        self._last_eval: dict[str, datetime] = {}
        self._last_odds: datetime | None = None
        self._et_day = None

    # -- helpers -----------------------------------------------------------
    def _safe(self, label: str, fn: Callable, report: CycleReport):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the loop
            log.warning("%s failed: %s", label, exc)
            report.errors.append(f"{label}: {exc}")
            return None

    def _record_decision(self, ml: Moneyline, d: Decision, now: datetime, **extra) -> int:
        q = d.quote
        with self.store.conn:
            cur = self.store.conn.execute(
                """
                INSERT INTO decisions (known_at, game_date, home, away, market_slug, model,
                    action, side_team, p_model, p_play, p_sit, p_market_pm, p_market_sb,
                    edge, all_in_price, contracts, stake, reasons_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    to_iso(now),
                    ml.game_date_et,
                    ml.home,
                    ml.away,
                    ml.market_slug,
                    self.model_name,
                    d.action,
                    d.side_team,
                    d.p_side,
                    d.p_side_play,
                    d.p_side_sit,
                    d.market_mid,
                    d.sportsbook,
                    q.edge if q else None,
                    q.all_in_price if q else None,
                    d.contracts if d.action == "bet" else None,
                    d.stake if d.action == "bet" else None,
                    json.dumps(d.reasons + extra.get("notes", [])),
                ),
            )
        return cur.lastrowid

    def _ingest_news(self, games: list[Moneyline], now: datetime) -> int:
        """Only articles about teams playing soon go to the LLM; rosters gate its output."""
        from edgebot.model.strength import rosters_as_of
        from edgebot.names import name_key

        teams = {t for ml in games for t in (ml.home, ml.away)}
        today = now.astimezone(ET).date()
        rosters = {
            team: {name_key(name) for name, _ in players.values()}
            for team, players in rosters_as_of(self.store, today, now).items()
            if team in teams
        }
        return self.news(teams, rosters)

    # -- one game ----------------------------------------------------------
    def evaluate(self, ml: Moneyline, book: OrderBook, now: datetime) -> Decision:
        f = self.builder.build(game_date=ml.game_date_et, home=ml.home, away=ml.away, as_of=now)
        if f.market_logit is None:
            d = Decision("pass", ["no sportsbook line known yet; the model needs its market prior"])
            self._record_decision(ml, d, now)
            return d
        p, p_play, p_sit = predict_scenarios(self.model, f)
        self.store.save_prediction(
            game_date=ml.game_date_et,
            home=ml.home,
            away=ml.away,
            as_of=now,
            model=self.model_name,
            p_home=p,
            p_home_play=p_play,
            p_home_sit=p_sit,
            p_market=f.market_prob,
            known_at=now,
        )
        view = GameView(
            market=ml,
            book=book,
            p_home=p,
            p_home_play=p_play,
            p_home_sit=p_sit,
            has_uncertainty=bool(f.has_uncertainty),
            p_home_sportsbook=f.market_prob,
        )
        theta = ml.fee_coefficient or self.cfg.default_theta
        d = decide(
            view,
            bankroll=self.risk.state.bankroll,
            theta=theta,
            edge_cfg=self.cfg.edge,
            sizing_cfg=self.cfg.sizing,
        )
        key = game_key(ml.game_date_et, ml.home, ml.away)
        if d.action == "bet":
            rd = self.risk.check(game_key=key, market_slug=ml.market_slug, stake=d.stake, now=now)
            if not rd.allowed:
                d.action, d.reasons = "pass", d.reasons + [f"risk: {r}" for r in rd.reasons]
            elif rd.stake < d.stake - 1e-9:
                qty = math.floor(rd.stake / d.quote.all_in_price * 100) / 100
                q = quote_side(d.p_side, book.asks_for(d.long_side), qty, theta, self.cfg.edge)
                if q is None or qty < 0.01:
                    d.action, d.reasons = "pass", d.reasons + ["risk cap left no viable size"]
                else:
                    d.quote = q
                    d.reasons += [f"risk: {r}" for r in rd.reasons]
        decision_id = self._record_decision(ml, d, now)
        if d.action == "bet":
            res = self.executor.submit(
                OrderRequest(
                    decision_id=decision_id,
                    market_slug=ml.market_slug,
                    side_team=d.side_team,
                    long_side=d.long_side,
                    qty=d.contracts,
                    limit_price=d.quote.worst_price,
                    game_date=ml.game_date_et,
                    home=ml.home,
                    away=ml.away,
                    tip_utc=ml.start_utc,
                )
            )
            if res.filled_qty > 0:
                self.risk.record_fill(
                    game_key=key, market_slug=ml.market_slug, cost=res.cost + res.fees
                )
            elif res.status == "unknown":
                # we can't tell whether it filled: assume it did, so exposure and the
                # one-position-per-game rule stay conservative until reconciled
                self.risk.record_fill(
                    game_key=key, market_slug=ml.market_slug, cost=d.contracts * d.quote.worst_price
                )
                log_risk_event(
                    self.store, "order_unknown", f"{key}: reconcile order {res.order_id}", now
                )
            log.info("%s %s: %s (%s)", key, d.side_team, res.status, d.reasons[0])
        return d

    # -- one cycle ---------------------------------------------------------
    def cycle(self) -> CycleReport:
        now = self.clock()
        report = CycleReport()
        if self.risk.killed():
            n = self.executor.cancel_all()
            log_risk_event(self.store, "kill_switch", f"cancelled {n} open orders; stopping", now)
            save_risk_state(self.store, self.risk.state, now, mode=self.executor.mode)
            raise KillSwitchError("kill switch engaged")

        today = now.astimezone(ET).date()
        if self._et_day != today:
            if self._et_day is not None:
                self.risk.new_day(now=now)
            self._et_day = today

        # box scores are treated as known at 08:00 UTC; pull them once a day after that
        results_due = self.results is not None and now.hour >= 9 and self._results_day != now.date()
        if results_due and self._safe("results", self.results, report) is not None:
            self._results_day = now.date()

        def on_settle(row: dict) -> None:
            self.risk.record_settlement(cost=row["cost"] + row["fees"], pnl=row["pnl"], now=now)

        settled = self._safe(
            "settlement", lambda: settle_positions(self.store, now, on_settle), report
        )
        report.settled = len(settled or [])
        was_shutdown = self.risk.state.shutdown

        new_injuries = self._safe("injuries", self.injuries, report) if self.injuries else 0
        odds_due = self.odds is not None and (
            self._last_odds is None or now - self._last_odds >= self.cfg.odds_every
        )
        if odds_due and self._safe("odds", self.odds, report) is not None:
            self._last_odds = now

        moneylines = self._safe("moneylines", self.pm.nba_moneylines, report) or []
        in_window = [
            ml
            for ml in moneylines
            if self.cfg.window_close <= ml.start_utc - now <= self.cfg.window_open
        ]
        if self.news and in_window:
            new_news = self._safe("news", lambda: self._ingest_news(in_window, now), report)
            new_injuries = (new_injuries or 0) + (new_news or 0)

        for ml in moneylines:
            to_tip = ml.start_utc - now
            if not timedelta(0) < to_tip <= self.cfg.snapshot_horizon:
                continue
            book = self._safe(
                f"book {ml.market_slug}", lambda ml=ml: self.pm.book(ml.market_slug), report
            )
            if book is None:
                continue
            snapshot_to_store(self.store, ml, book)
            report.snapshots += 1
            if not self.cfg.window_close <= to_tip <= self.cfg.window_open:
                continue
            key = game_key(ml.game_date_et, ml.home, ml.away)
            last = self._last_eval.get(key)
            due = last is None or new_injuries or now - last >= self.cfg.reevaluate_every
            if not due:
                continue
            self._last_eval[key] = now
            d = self._safe(
                f"evaluate {key}", lambda ml=ml, book=book: self.evaluate(ml, book, now), report
            )
            report.evaluated += 1
            report.bets += int(d is not None and d.action == "bet")

        # once a day, after the morning settlement (15:00 UTC = 10/11am ET)
        report_due = (
            self.daily_report is not None and now.hour >= 15 and self._report_day != now.date()
        )
        if report_due and self._safe("daily report", self.daily_report, report) is not None:
            self._report_day = now.date()

        if self.risk.state.shutdown and not was_shutdown:
            log_risk_event(self.store, "shutdown", self.risk.state.shutdown_reason, now)
        save_risk_state(self.store, self.risk.state, now, mode=self.executor.mode)
        return report

    def run(self, interval_s: float, sleep: Callable[[float], None] = time.sleep) -> None:
        while True:
            try:
                r = self.cycle()
                log.info(
                    "cycle: %d snapshots, %d evaluated, %d bets, %d settled, %d errors",
                    r.snapshots, r.evaluated, r.bets, r.settled, len(r.errors),
                )  # fmt: skip
            except KillSwitchError:
                log.warning("kill switch engaged: open orders cancelled, loop stopped")
                return
            sleep(interval_s)
