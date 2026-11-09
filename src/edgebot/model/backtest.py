"""Strict walk-forward backtest by date.

Protocol:

1. **Dataset**: one row per completed game with a sportsbook closing line.
   Features are built as of 7pm ET on game day (the assumed decision time,
   matching how archived closing lines are stamped). Games with a known tip
   before 7:05pm ET are skipped, so nothing near or after tip can leak in.
2. **Walk-forward**: within each evaluated season, models are refit every
   ``refit_days`` using only games dated *before* the block being predicted.
3. **Selection**: models are ranked by log loss on *validation* seasons
   only. The test seasons are reported but never used to choose or tune.
4. **Baseline**: every result is shown next to the market-only model. If the
   chosen model doesn't beat it on test-season log loss, the report says so.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from edgebot.model import metrics
from edgebot.model.features import MODEL_FEATURES, FeatureBuilder
from edgebot.model.game_model import default_models
from edgebot.timeutil import et_date_to_utc, parse_iso

log = logging.getLogger(__name__)

DECISION_TIME_ET = time(19, 0)


def build_dataset(
    store,
    seasons: Iterable[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Feature rows for every completed game (optionally limited to ``seasons``)."""
    games = store.query("""
        SELECT g.game_id, g.season, g.game_date, g.tip_utc, g.home, g.away,
               r.home_pts, r.away_pts
        FROM games g JOIN game_results r USING (game_id)
        ORDER BY g.game_date, g.game_id
        """)
    wanted = set(seasons) if seasons else None
    builder = FeatureBuilder(store)
    rows, skipped_early = [], 0
    current_day = None
    for g in games:
        if wanted and g["season"] not in wanted:
            continue
        game_day = date.fromisoformat(g["game_date"])
        as_of = et_date_to_utc(game_day, DECISION_TIME_ET)
        if g["tip_utc"] and parse_iso(g["tip_utc"]) < as_of + timedelta(minutes=5):
            skipped_early += 1
            continue
        if progress and g["game_date"] != current_day:
            current_day = g["game_date"]
            progress(current_day)
        f = builder.build(game_date=g["game_date"], home=g["home"], away=g["away"], as_of=as_of)
        rec = f.to_record()
        rec.update(season=g["season"], game_id=g["game_id"], y=int(g["home_pts"] > g["away_pts"]))
        rows.append(rec)
    if skipped_early:
        log.info("skipped %d games tipping before the decision time", skipped_early)
    return pd.DataFrame(rows)


def walk_forward(
    df: pd.DataFrame,
    seasons: Iterable[str],
    models: dict[str, Callable] | None = None,
    refit_days: int = 30,
    min_train: int = 400,
) -> pd.DataFrame:
    """Out-of-sample predictions for every game in ``seasons``."""
    models = models or default_models()
    data = (
        df[df["market_logit"].notna()].sort_values(["game_date", "game_id"]).reset_index(drop=True)
    )
    out = []
    for season in seasons:
        season_rows = data[data["season"] == season]
        if season_rows.empty:
            log.warning("season %s has no rows with a market price; skipped", season)
            continue
        start = date.fromisoformat(season_rows["game_date"].min())
        end = date.fromisoformat(season_rows["game_date"].max())
        block_start = start
        while block_start <= end:
            block_end = block_start + timedelta(days=refit_days)
            train = data[data["game_date"] < block_start.isoformat()]
            block = season_rows[
                (season_rows["game_date"] >= block_start.isoformat())
                & (season_rows["game_date"] < block_end.isoformat())
            ]
            block_start = block_end
            if block.empty:
                continue
            if len(train) < min_train:
                log.warning(
                    "only %d training rows before %s; block skipped",
                    len(train),
                    block.iloc[0]["game_date"],
                )
                continue
            preds = block[
                ["game_id", "game_date", "season", "home", "away", "y", "market_prob"]
            ].copy()
            y_train = train["y"].to_numpy()
            for name, factory in models.items():
                model = factory().fit(train, y_train)
                preds[f"p_{name}"] = model.predict(block)
            out.append(preds)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


@dataclass
class SeasonScore:
    season: str
    model: str
    n: int
    log_loss: float
    brier: float
    ece: float
    accuracy: float


def score(preds: pd.DataFrame) -> list[SeasonScore]:
    names = [c[2:] for c in preds.columns if c.startswith("p_")]
    cols = {"market_raw": preds["market_prob"], **{n: preds[f"p_{n}"] for n in names}}
    out = []
    for season, idx in preds.groupby("season").groups.items():
        y = preds.loc[idx, "y"]
        for name, p in cols.items():
            pp = p.loc[idx]
            out.append(
                SeasonScore(
                    season=season,
                    model=name,
                    n=len(idx),
                    log_loss=metrics.log_loss(y, pp),
                    brier=metrics.brier(y, pp),
                    ece=metrics.ece(y, pp),
                    accuracy=metrics.accuracy(y, pp),
                )
            )
    return out


def select_model(scores: list[SeasonScore], validation_seasons: Iterable[str]) -> str:
    """Lowest mean validation log loss among fitted models (raw market excluded)."""
    val = set(validation_seasons)
    by_model: dict[str, list[float]] = {}
    for s in scores:
        if s.season in val and s.model != "market_raw":
            by_model.setdefault(s.model, []).append(s.log_loss)
    if not by_model:
        raise ValueError("no validation scores")
    return min(by_model, key=lambda m: np.mean(by_model[m]))


def calibration_plot(preds: pd.DataFrame, models: list[str], path: str | Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5.5, 5.5), dpi=120)
    ax.plot([0, 1], [0, 1], color="#999999", lw=1, ls="--", label="perfect")
    for name in models:
        col = "market_prob" if name == "market_raw" else f"p_{name}"
        bins = metrics.calibration_bins(preds["y"], preds[col], n_bins=10)
        ax.plot([b[0] for b in bins], [b[1] for b in bins], marker="o", lw=1.5, label=name)
    ax.set_xlabel("predicted home win probability")
    ax.set_ylabel("observed home win rate")
    ax.set_title(title)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def report_markdown(
    scores: list[SeasonScore],
    selected: str,
    validation: list[str],
    test: list[str],
    plot_name: str | None = None,
    note: str = "",
) -> str:
    lines = ["# Backtest results", ""]
    if note:
        lines += [note, ""]
    lines += [
        f"Validation seasons (model selection): {', '.join(validation)}",
        f"Test seasons (reported only, never tuned on): {', '.join(test)}",
        f"Selected model: **{selected}** (lowest validation log loss)",
        "",
        "| Season | Model | Games | Log loss | Brier | ECE | Accuracy |",
        "| - | - | - | - | - | - | - |",
    ]
    for s in sorted(scores, key=lambda s: (s.season, s.log_loss)):
        tag = " (selected)" if s.model == selected else ""
        lines.append(
            f"| {s.season} | {s.model}{tag} | {s.n} | {s.log_loss:.4f} | {s.brier:.4f} "
            f"| {s.ece:.4f} | {s.accuracy:.3f} |"
        )
    lines.append("")
    if selected == "market":
        lines.append(
            "- **No feature model beat the market-only baseline on validation**, so the "
            "baseline itself was selected. The model adds nothing over the market here; any "
            "edge edgebot finds would come only from Polymarket pricing away from sportsbooks."
        )
    for season in test if selected != "market" else []:
        by = {s.model: s for s in scores if s.season == season}
        if selected in by and "market" in by:
            d = by["market"].log_loss - by[selected].log_loss
            verdict = (
                f"beats the market-only baseline by {d:.4f} log loss"
                if d > 0
                else f"does **not** beat the market-only baseline ({-d:.4f} worse log loss)"
            )
            lines.append(f"- {season}: {selected} {verdict}.")
    if plot_name:
        lines += ["", f"![calibration]({plot_name})"]
    lines += [
        "",
        "Accuracy is shown for context only. Models are selected on log loss.",
        f"Features: {', '.join(MODEL_FEATURES)}.",
    ]
    return "\n".join(lines) + "\n"
