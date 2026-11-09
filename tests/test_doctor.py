import json
from datetime import date

from edgebot.config import Secret, Settings
from edgebot.doctor import data_checks, network_checks, render
from edgebot.trading.risk import RiskState
from edgebot.trading.state import save_risk_state
from tests.synthetic import load_into_store, make_league


def _levels(checks):
    return {c.name: c.level for c in checks}


def test_empty_setup_fails_with_actionable_hints(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checks = data_checks(store, Settings(db_path=tmp_path / "data" / "x.db"), tmp_path / "models")
    lv = _levels(checks)
    assert lv["database"] == "ok"
    for name in ("nba results", "historical closing lines", "trained model", "odds api key"):
        assert lv[name] == "fail", name
    assert lv["anthropic api key"] == "warn"
    text = render(checks)
    assert "edgebot ingest nba" in text and "edgebot train" in text


def test_ready_setup_passes_and_flags_market_only_model(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for i in range(3):
        league = make_league(n_games=60, seed=1, game_seed=i, id_prefix=f"0022{3 + i}",
                             start=date(2023 + i, 10, 22))  # fmt: skip
        load_into_store(store, league, f"{2023 + i}-{24 + i}")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "game_model.pkl").write_bytes(b"x")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "model_choice.json").write_text(json.dumps({"model": "market"}))
    settings = Settings(db_path=tmp_path / "data" / "x.db", odds_api_key=Secret("k"))
    lv = _levels(data_checks(store, settings, tmp_path / "models"))
    assert lv["nba results"] == lv["historical closing lines"] == lv["odds api key"] == "ok"
    assert lv["trained model"] == "warn"  # market-only model selected


def test_shutdown_and_kill_switch_are_reported(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "KILL").write_text("x")
    save_risk_state(store, RiskState(bankroll=1, peak=2, day_start=1, shutdown=True))
    lv = _levels(data_checks(store, Settings(db_path=tmp_path / "x.db"), tmp_path))
    assert lv["kill switch"] == "warn" and lv["risk breakers"] == "fail"


def test_network_checks_interpret_statuses():
    def probe(url):
        if "stats.nba" in url:
            return 403
        if "official.nba" in url:
            raise ConnectionError("blocked")
        if "odds-api" in url:
            return 401
        return 200

    lv = _levels(network_checks(probe))
    assert lv == {
        "polymarket us gateway": "ok",
        "nba stats": "fail",
        "nba injury report page": "fail",
        "the odds api": "ok",
    }
