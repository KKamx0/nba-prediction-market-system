"""Weekly retraining that has to earn its place.

The current ("champion") model was trained on games up to ``train_end``. Games
played since then are a genuine out-of-sample test for it. The question
retraining has to answer is: *would refitting on new results have predicted
those same games better?*

To answer it honestly, the challenger is scored walk-forward: each two-week
block of new games is predicted by a model fit only on games before that
block. If the challenger's log loss on the new games is no worse than the
champion's, it's refit on everything and promoted; otherwise the champion
stays. Every attempt is recorded in ``model_versions``.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from edgebot.model.backtest import build_dataset
from edgebot.model.game_model import default_models, load_model, save_model
from edgebot.model.metrics import log_loss
from edgebot.timeutil import to_iso


@dataclass(frozen=True)
class RetrainResult:
    promoted: bool
    reason: str
    n_new: int = 0
    champion_log_loss: float | None = None
    challenger_log_loss: float | None = None


def update_dataset(store, cache_path: Path) -> pd.DataFrame:
    """Extend the cached feature dataset with games that finished since it was built."""
    old = pd.read_csv(cache_path, dtype={"game_id": str}) if cache_path.exists() else pd.DataFrame()
    have = set(old["game_id"]) if not old.empty else set()
    new = build_dataset(store, exclude_game_ids=have)
    df = pd.concat([old, new], ignore_index=True) if not new.empty else old
    if not df.empty:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
    return df


def compare(
    df: pd.DataFrame,
    champion,
    factory: Callable,
    train_end: str,
    *,
    block_days: int = 14,
    min_new: int = 60,
) -> tuple[int, float | None, float | None]:
    """(n new games, champion log loss, walk-forward challenger log loss) on games after train_end."""
    data = (
        df[df["market_logit"].notna()].sort_values(["game_date", "game_id"]).reset_index(drop=True)
    )
    new = data[data["game_date"] > train_end]
    if len(new) < min_new:
        return len(new), None, None
    champ_p = champion.predict(new)
    chall_p = np.empty(len(new))
    start = date.fromisoformat(new["game_date"].min())
    end = date.fromisoformat(new["game_date"].max())
    pos = 0
    while start <= end:
        stop = start + timedelta(days=block_days)
        block = new[(new["game_date"] >= start.isoformat()) & (new["game_date"] < stop.isoformat())]
        if not block.empty:
            train = data[data["game_date"] < start.isoformat()]
            chall_p[pos : pos + len(block)] = (
                factory().fit(train, train["y"].to_numpy()).predict(block)
            )
            pos += len(block)
        start = stop
    y = new["y"].to_numpy()
    return len(new), log_loss(y, champ_p), log_loss(y, chall_p)


def retrain_if_better(
    store,
    *,
    cache_path: Path,
    model_path: Path,
    choice_path: Path,
    now: datetime,
    min_new: int = 60,
) -> RetrainResult:
    def record(r: RetrainResult, name: str, train_end: str) -> RetrainResult:
        with store.conn:
            store.conn.execute(
                """
                INSERT INTO model_versions (known_at, model, train_end, n_evaluated,
                    champion_log_loss, challenger_log_loss, promoted, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (to_iso(now), name, train_end, r.n_new, r.champion_log_loss,
                 r.challenger_log_loss, int(r.promoted), r.reason),
            )  # fmt: skip
        return r

    if not model_path.exists() or not choice_path.exists():
        return RetrainResult(False, "no trained model yet; run edgebot train")
    meta = json.loads(choice_path.read_text())
    name, train_end = meta["model"], meta.get("train_end")
    if not train_end:
        return RetrainResult(False, "model has no train_end; rerun edgebot train once")
    factory = default_models()[name]
    df = update_dataset(store, cache_path)
    if df.empty:
        return record(RetrainResult(False, "no training data"), name, train_end)
    n, champ, chall = compare(df, load_model(model_path), factory, train_end, min_new=min_new)
    if champ is None:
        return record(
            RetrainResult(False, f"only {n} new games since {train_end} (need {min_new})", n),
            name,
            train_end,
        )
    if chall > champ:
        return record(
            RetrainResult(
                False,
                "retraining would have predicted new games worse; kept champion",
                n,
                champ,
                chall,
            ),
            name,
            train_end,
        )
    data = df[df["market_logit"].notna()].sort_values(["game_date", "game_id"])
    shutil.copyfile(model_path, model_path.with_suffix(".prev.pkl"))  # one-step rollback
    save_model(factory().fit(data, data["y"].to_numpy()), model_path)
    new_end = str(data["game_date"].max())
    meta["train_end"] = new_end
    choice_path.write_text(json.dumps(meta))
    return record(
        RetrainResult(
            True,
            f"retrained through {new_end}: log loss {chall:.4f} vs {champ:.4f}",
            n,
            champ,
            chall,
        ),
        name,
        new_end,
    )
