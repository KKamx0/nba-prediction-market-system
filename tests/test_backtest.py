from datetime import date

import numpy as np
import pandas as pd
import pytest

from edgebot.db import Store
from edgebot.model.backtest import (
    build_dataset,
    calibration_plot,
    report_markdown,
    score,
    select_model,
    walk_forward,
)
from edgebot.model.game_model import Calibrated, LogisticModel, MarketOnly
from edgebot.model.metrics import log_loss
from tests.synthetic import load_into_store, make_league

FAST_MODELS = {
    "market": lambda: MarketOnly(),
    "logistic+platt": lambda: Calibrated(LogisticModel(), "platt"),
}


@pytest.fixture(scope="module")
def dataset():
    """Two seasons of one synthetic league, with a deliberately bad market.

    With a realistic market (logit noise 0.6, ~0.9 correlation with the truth)
    the features only shave ~0.002 log loss off the market: our strength
    estimate tracks the truth less well than the market does. That's the honest
    expectation for real data too. A bad market (noise 1.0) is used here so the
    test can check that the features carry *incremental* signal at all.
    """
    store = Store(":memory:")
    for i, (season, start) in enumerate(
        [("2024-25", date(2024, 10, 22)), ("2025-26", date(2025, 10, 21))]
    ):
        league = make_league(
            n_games=600, seed=11, game_seed=100 + i, id_prefix=f"0022{4 + i}", start=start
        )
        load_into_store(store, league, season, market_noise=1.0, odds_seed=i)
    df = build_dataset(store)
    store.close()
    return df


def test_dataset_has_one_row_per_game_with_market(dataset):
    assert len(dataset) == dataset["game_id"].nunique() > 1000
    assert dataset["market_prob"].notna().mean() > 0.99
    assert set(dataset["season"]) == {"2024-25", "2025-26"}


def test_every_refit_trains_only_on_earlier_games(dataset):
    seen = []

    class Spy(MarketOnly):
        def fit(self, X, y):
            self.max_train = X["game_date"].max()
            return super().fit(X, y)

        def predict(self, X):
            seen.append((self.max_train, X["game_date"].min()))
            return super().predict(X)

    preds = walk_forward(dataset, ["2025-26"], {"spy": lambda: Spy()}, refit_days=20)
    assert len(preds) > 0 and set(preds["season"]) == {"2025-26"}
    assert all(max_train < first_pred for max_train, first_pred in seen)


def test_features_add_value_over_a_noisy_market(dataset):
    preds = walk_forward(dataset, ["2025-26"], FAST_MODELS)
    y = preds["y"]
    assert log_loss(y, preds["p_logistic+platt"]) < log_loss(y, preds["p_market"]) - 0.01


def test_shuffled_labels_collapse_to_no_skill(dataset):
    """If future outcomes are shuffled away from their features, skill must vanish."""
    rng = np.random.default_rng(0)
    shuffled = dataset.copy()
    shuffled["y"] = rng.permutation(shuffled["y"].to_numpy())
    real = walk_forward(dataset, ["2025-26"], FAST_MODELS)
    fake = walk_forward(shuffled, ["2025-26"], FAST_MODELS)
    y_true = real["y"].to_numpy()
    base_rate = np.full(len(y_true), dataset["y"].mean())
    no_skill = log_loss(y_true, base_rate)
    real_ll = log_loss(y_true, real["p_logistic+platt"])
    fake_ll = log_loss(y_true, fake["p_logistic+platt"].to_numpy())
    assert real_ll < no_skill - 0.05  # real features carry signal
    assert fake_ll > no_skill - 0.01  # shuffled training gives none


def test_scoring_selection_and_report(dataset, tmp_path):
    preds = walk_forward(dataset, ["2025-26"], FAST_MODELS)
    scores = score(preds)
    models = {s.model for s in scores}
    assert models == {"market_raw", "market", "logistic+platt"}
    chosen = select_model(scores, ["2025-26"])
    assert chosen == "logistic+platt"
    with pytest.raises(ValueError):
        select_model(scores, ["1999-00"])

    md = report_markdown(scores, chosen, ["2025-26"], ["2025-26"], plot_name="cal.png")
    assert "beats the market-only baseline" in md
    assert "Accuracy is shown for context only" in md
    plot = tmp_path / "cal.png"
    calibration_plot(preds, ["market_raw", "logistic+platt"], plot, "test")
    assert plot.stat().st_size > 1000


def test_report_says_so_plainly_when_the_model_loses():
    from edgebot.model.backtest import SeasonScore

    scores = [
        SeasonScore("2025-26", "market", 100, 0.60, 0.2, 0.02, 0.6),
        SeasonScore("2025-26", "logistic+platt", 100, 0.62, 0.21, 0.03, 0.6),
    ]
    md = report_markdown(scores, "logistic+platt", ["2024-25"], ["2025-26"])
    assert "does **not** beat the market-only baseline" in md


def test_walk_forward_skips_seasons_without_markets():
    df = pd.DataFrame(
        {
            "game_id": ["1"],
            "game_date": ["2025-11-01"],
            "season": ["2025-26"],
            "market_logit": [np.nan],
        }
    )
    assert walk_forward(df, ["2025-26"], FAST_MODELS).empty


def test_report_when_the_market_itself_wins_validation():
    from edgebot.model.backtest import SeasonScore

    scores = [
        SeasonScore("2025-26", "market", 100, 0.60, 0.2, 0.02, 0.6),
        SeasonScore("2025-26", "logistic+platt", 100, 0.62, 0.21, 0.03, 0.6),
    ]
    md = report_markdown(scores, "market", ["2024-25"], ["2025-26"])
    assert "No feature model beat the market-only baseline" in md
    assert "-0.0000" not in md
