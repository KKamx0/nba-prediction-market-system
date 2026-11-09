import logging

import pandas as pd
import pytest

from edgebot.cli import build_parser, main
from edgebot.db import Store


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no stray .env is picked up
    monkeypatch.setenv("EDGEBOT_DB_PATH", str(tmp_path / "data" / "edge.db"))
    monkeypatch.setenv("EDGEBOT_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def test_init_db_creates_database(env, capsys):
    assert main(["init-db"]) == 0
    assert (env / "data" / "edge.db").exists()
    assert "schema v1" in capsys.readouterr().out


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
    (row,) = store.query("SELECT home, away, kind FROM odds_quotes")
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
