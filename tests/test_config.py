import logging

from edgebot.config import RedactingFilter, Secret, Settings


def test_secret_never_prints_its_value():
    s = Secret("placeholder-very-secret")
    assert "very-secret" not in repr(s)
    assert "very-secret" not in str(s)
    assert "very-secret" not in f"{s}"
    assert s.get() == "placeholder-very-secret"
    assert not Secret("")


def test_settings_from_env_reads_flags_and_masks(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "placeholder-value")
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EDGEBOT_DB_PATH", str(tmp_path / "x.db"))
    s = Settings.from_env(dotenv_path=None)
    assert s.live_trading is True
    assert s.odds_api_key.get() == "placeholder-value"
    assert "placeholder-value" not in repr(s)
    assert s.db_path == tmp_path / "x.db"


def test_live_trading_defaults_off(monkeypatch):
    for name in ("LIVE_TRADING",):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env(dotenv_path=None)
    assert not s.live_trading


def test_redacting_filter_masks_secrets_in_messages_and_args():
    flt = RedactingFilter(["placeholder-value", "abc"])  # too-short values are ignored
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "GET /odds?apiKey=%s failed", ("placeholder-value",), None
    )
    flt.filter(record)
    assert record.getMessage() == "GET /odds?apiKey=*** failed"
