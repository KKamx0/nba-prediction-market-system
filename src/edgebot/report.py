"""Performance report: P&L, CLV, calibration vs the market, drawdown.

Closing line value is the headline metric. ROI over a few hundred bets is
mostly noise; consistently buying below the closing price is the best
available evidence of real edge.

``compute`` returns a ``Summary`` used three ways: the daily report (Discord),
the sanitized ``docs/results.md`` export, and the paper -> live gate. Because
the gate reads the same numbers, the report and the gate can't disagree.

The export contains aggregates only: no account details, no per-bet prices,
no raw Polymarket data.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from edgebot.model import metrics
from edgebot.timeutil import parse_iso, to_iso, utc_now


@dataclass
class Summary:
    mode: str
    generated_at: datetime
    n_bets: int
    n_settled: int
    n_open: int
    n_passes: int
    n_reviews: int
    staked: float  # cost + fees of settled positions
    pnl: float
    roi: float | None
    win_rate: float | None
    clv_mean: float | None  # closing price - our price, per contract
    clv_ci: tuple[float, float] | None  # 95% interval for the mean
    clv_n: int
    first_bet: datetime | None
    last_bet: datetime | None
    max_drawdown: float  # fraction of peak equity
    n_scored: int  # games with a pre-tip prediction and a result
    model_log_loss: float | None
    market_log_loss: float | None
    model_brier: float | None
    market_brier: float | None
    model_ece: float | None

    @property
    def weeks_active(self) -> float:
        if not self.first_bet or not self.last_bet:
            return 0.0
        return (self.last_bet - self.first_bet).total_seconds() / (7 * 86400)

    @property
    def beats_market(self) -> bool | None:
        if self.model_log_loss is None or self.market_log_loss is None:
            return None
        return self.model_log_loss < self.market_log_loss


def _mean_ci(xs: list[float]) -> tuple[float, float] | None:
    if len(xs) < 2:
        return None
    m, sd = statistics.fmean(xs), statistics.stdev(xs)
    half = 1.96 * sd / math.sqrt(len(xs))
    return m - half, m + half


def max_drawdown(pnls: list[float], starting_bankroll: float) -> float:
    equity, peak, worst = starting_bankroll, starting_bankroll, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, 1 - equity / peak)
    return worst


def scored_predictions(store, since_date: str = "0000-00-00") -> list[tuple[int, float, float]]:
    """(home_won, model p_home, market p_home), using each game's last prediction before tip."""
    rows = store.query("""
        SELECT p.game_date, p.home, p.away, p.p_home, p.p_market, r.home_pts, r.away_pts
        FROM predictions p
        JOIN games g ON g.game_date = p.game_date AND g.home = p.home AND g.away = p.away
        JOIN game_results r ON r.game_id = g.game_id
        WHERE p.p_market IS NOT NULL AND (g.tip_utc IS NULL OR p.as_of < g.tip_utc)
        ORDER BY p.as_of
        """)
    latest: dict[tuple[str, str, str], tuple[int, float, float]] = {}
    for r in rows:  # ordered by as_of, so the last write per game wins
        latest[(r["game_date"], r["home"], r["away"])] = (
            int(r["home_pts"] > r["away_pts"]),
            r["p_home"],
            r["p_market"],
        )
    return list(latest.values())


def compute(
    store,
    *,
    mode: str = "paper",
    starting_bankroll: float = 1000.0,
    since_date: str = "0000-00-00",
) -> Summary:
    """Summary over positions and predictions for games on or after ``since_date``."""
    pos = store.query(
        "SELECT * FROM positions WHERE mode = ? AND game_date >= ? ORDER BY opened_at",
        (mode, since_date),
    )
    settled = [p for p in pos if p["settled_at"]]
    staked = sum(p["cost"] + p["fees"] for p in settled)
    pnl = sum(p["pnl"] for p in settled)
    clv = [
        p["closing_price"] - p["cost"] / p["qty"]
        for p in pos
        if p["closing_price"] is not None and p["qty"] > 0
    ]
    by_settle = sorted(settled, key=lambda p: p["settled_at"])
    decisions = store.query("SELECT action, COUNT(*) AS n FROM decisions GROUP BY action")
    counts = {r["action"]: r["n"] for r in decisions}
    scored = scored_predictions(store, since_date)
    y = [s[0] for s in scored]
    pm = [s[1] for s in scored]
    pk = [s[2] for s in scored]
    has_scores = len(scored) >= 1
    return Summary(
        mode=mode,
        generated_at=utc_now(),
        n_bets=len(pos),
        n_settled=len(settled),
        n_open=len(pos) - len(settled),
        n_passes=counts.get("pass", 0),
        n_reviews=counts.get("review", 0),
        staked=staked,
        pnl=pnl,
        roi=pnl / staked if staked else None,
        win_rate=sum(1 for p in settled if p["won"]) / len(settled) if settled else None,
        clv_mean=statistics.fmean(clv) if clv else None,
        clv_ci=_mean_ci(clv),
        clv_n=len(clv),
        first_bet=parse_iso(pos[0]["opened_at"]) if pos else None,
        last_bet=parse_iso(pos[-1]["opened_at"]) if pos else None,
        max_drawdown=max_drawdown([p["pnl"] for p in by_settle], starting_bankroll),
        n_scored=len(scored),
        model_log_loss=metrics.log_loss(y, pm) if has_scores else None,
        market_log_loss=metrics.log_loss(y, pk) if has_scores else None,
        model_brier=metrics.brier(y, pm) if has_scores else None,
        market_brier=metrics.brier(y, pk) if has_scores else None,
        model_ece=metrics.ece(y, pm) if has_scores else None,
    )


def _fmt(x: float | None, spec: str, suffix: str = "") -> str:
    return "n/a" if x is None else f"{x:{spec}}{suffix}"


def to_text(s: Summary) -> str:
    ci = f"[{s.clv_ci[0]:+.3f}, {s.clv_ci[1]:+.3f}]" if s.clv_ci else "n/a"
    beats = {True: "model better", False: "market better", None: "n/a"}[s.beats_market]
    return "\n".join(
        [
            f"edgebot {s.mode} report, {s.generated_at:%Y-%m-%d %H:%M} UTC",
            f"bets {s.n_bets} ({s.n_settled} settled, {s.n_open} open) | passes {s.n_passes} | "
            f"flagged for review {s.n_reviews}",
            f"P&L ${s.pnl:+,.2f} on ${s.staked:,.2f} staked | ROI {_fmt(s.roi, '+.1%')} | "
            f"win rate {_fmt(s.win_rate, '.1%')}",
            f"CLV {_fmt(s.clv_mean, '+.4f')} per contract (95% CI {ci}, n={s.clv_n})",
            f"log loss model {_fmt(s.model_log_loss, '.4f')} vs market "
            f"{_fmt(s.market_log_loss, '.4f')} ({beats}, n={s.n_scored}) | Brier "
            f"{_fmt(s.model_brier, '.4f')} vs {_fmt(s.market_brier, '.4f')}",
            f"max drawdown {s.max_drawdown:.1%} | active {s.weeks_active:.1f} weeks",
        ]
    )


def calibration_png(store, path: str | Path) -> bool:
    """Model vs market calibration on scored games. False if there's nothing to plot."""
    scored = scored_predictions(store)
    if len(scored) < 20:
        return False
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    y = [s[0] for s in scored]
    fig, ax = plt.subplots(figsize=(5, 5), dpi=120)
    ax.plot([0, 1], [0, 1], color="#999999", lw=1, ls="--", label="perfect")
    for label, idx in (("model", 1), ("sportsbook market", 2)):
        bins = metrics.calibration_bins(y, [s[idx] for s in scored], n_bins=10)
        ax.plot([b[0] for b in bins], [b[1] for b in bins], marker="o", lw=1.5, label=label)
    ax.set(xlim=(0, 1), ylim=(0, 1), xlabel="predicted home win probability", ylabel="observed")
    ax.set_title(f"Live calibration (n={len(scored)})")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return True


def to_markdown(s: Summary, plot_name: str | None = None) -> str:
    """Sanitized export for the README: aggregates only."""
    ci = f"[{s.clv_ci[0]:+.4f}, {s.clv_ci[1]:+.4f}]" if s.clv_ci else "n/a"
    lines = [
        "# Results so far",
        "",
        f"_{s.mode.title()} trading. Generated {to_iso(s.generated_at)[:16]} UTC by "
        "`edgebot report --export`. Aggregates only: no account details or per-bet prices._",
        "",
        "| Metric | Value |",
        "| - | - |",
        f"| Bets placed | {s.n_bets} ({s.n_settled} settled) |",
        f"| Passes logged | {s.n_passes} |",
        f"| Flagged for review (model vs market > 15 pts) | {s.n_reviews} |",
        f"| **Closing line value** (per contract) | **{_fmt(s.clv_mean, '+.4f')}**, 95% CI {ci} |",
        f"| ROI after fees | {_fmt(s.roi, '+.1%')} |",
        f"| Max drawdown | {s.max_drawdown:.1%} |",
        f"| Log loss: model / sportsbook market | {_fmt(s.model_log_loss, '.4f')} / "
        f"{_fmt(s.market_log_loss, '.4f')} (n={s.n_scored}) |",
        f"| Brier: model / sportsbook market | {_fmt(s.model_brier, '.4f')} / "
        f"{_fmt(s.market_brier, '.4f')} |",
        f"| Active | {s.weeks_active:.1f} weeks |",
        "",
        "CLV is the primary metric. ROI over a small sample is mostly noise.",
    ]
    if s.beats_market is False:
        lines += ["", "**The model is not beating the market-only baseline on log loss.**"]
    if plot_name:
        lines += ["", f"![live calibration]({plot_name})"]
    return "\n".join(lines) + "\n"


def send_discord(webhook_url: str, text: str, client: httpx.Client | None = None) -> None:
    """Post the plain-text report. The URL is a secret and is never logged."""
    c = client or httpx.Client(timeout=15)
    resp = c.post(webhook_url, json={"content": f"```\n{text}\n```"[:1990]})
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook returned HTTP {resp.status_code}")
