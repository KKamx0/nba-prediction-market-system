import logging

import pandas as pd
import pytest

from edgebot.cli import build_parser, main
from edgebot.db import MIGRATIONS, Store


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no stray .env is picked up
    monkeypatch.setenv("EDGEBOT_DB_PATH", str(tmp_path / "data" / "edge.db"))
    monkeypatch.setenv("EDGEBOT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def test_init_db_creates_database(env, capsys):
    assert main(["init-db"]) == 0
    assert (env / "data" / "edge.db").exists()
    assert f"schema v{len(MIGRATIONS)}" in capsys.readouterr().out


def test_odds_history_import_end_to_end(env, capsys):
    csv = env / "odds.csv"
    pd.DataFrame(
        {
            "Date": ["2024-01-15"],
            "Home": ["Boston Celtics"],
            "Away": ["New York Knicks"],
            "HML": [-250],
            "AML": [210],
        }
    ).to_csv(csv, index=False)
    args = [
        "ingest", "odds-history", str(csv), "--layout", "wide", "--source", "test",
        "--date-col", "Date", "--home-col", "Home", "--away-col", "Away",
        "--home-odds-col", "HML", "--away-odds-col", "AML",
    ]  # fmt: skip
    assert main(args) == 0
    assert "1 closing lines imported" in capsys.readouterr().out
    store = Store(env / "data" / "edge.db")
    try:
        (row,) = store.query("SELECT home, away, kind FROM odds_quotes")
    finally:
        store.close()
    assert tuple(row) == ("BOS", "NYK", "closing")


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_api_key_never_reaches_logs(env, monkeypatch, caplog):
    import httpx
    import respx

    monkeypatch.setenv("ODDS_API_KEY", "placeholder-odds-value")
    with respx.mock:
        respx.get("https://api.the-odds-api.com/v4/sports/basketball_nba/odds").mock(
            return_value=httpx.Response(200, json=[], headers={"x-requests-remaining": "400"})
        )
        with caplog.at_level(logging.DEBUG):
            assert main(["-v", "snapshot", "odds"]) == 0
    assert "placeholder-odds-value" not in caplog.text


def test_backtest_and_train_end_to_end(env, capsys):
    from datetime import date

    from tests.synthetic import load_into_store, make_league

    store = Store(env / "data" / "edge.db")
    for i, season in enumerate(["2023-24", "2024-25", "2025-26"]):
        league = make_league(
            n_games=500, seed=21, game_seed=200 + i, id_prefix=f"0022{3 + i}",
            start=date(2023 + i, 10, 22),
        )  # fmt: skip
        load_into_store(store, league, season, market_noise=0.5, odds_seed=i)
    store.close()

    out = env / "report"
    rc = main(["backtest", "--validate", "2024-25", "--test", "2025-26", "--out", str(out)])
    assert rc == 0
    report = (out / "README.md").read_text()
    assert "Selected model" in report and "2025-26" in report
    assert "market-only baseline" in report
    assert (out / "calibration.png").exists()

    assert main(["train"]) == 0
    assert (env / "models" / "game_model.pkl").exists()
    assert "trained" in capsys.readouterr().out
