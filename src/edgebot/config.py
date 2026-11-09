"""Settings from the environment (.env), with secrets that can't leak by accident.

``Secret`` values print as ``Secret('***')``, so a stray ``print(settings)``
or an exception repr never shows a key. ``install_log_redaction`` adds a
logging filter that masks every configured secret in every log record, as a
second line of defense.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str | None) -> None:
        self._value = value or ""

    def get(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret('***')" if self._value else "Secret('')"

    __str__ = __repr__


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("data/edgebot.db")
    cache_dir: Path = Path("cache")
    log_level: str = "INFO"
    odds_api_key: Secret = field(default_factory=lambda: Secret(""))
    anthropic_api_key: Secret = field(default_factory=lambda: Secret(""))
    polymarket_key_id: Secret = field(default_factory=lambda: Secret(""))
    polymarket_secret_key: Secret = field(default_factory=lambda: Secret(""))
    discord_webhook_url: Secret = field(default_factory=lambda: Secret(""))
    live_trading: bool = False
    paper_bankroll: float = 1000.0
    live_bankroll_cap: float = 0.0  # max dollars live trading may use; 0 = live disabled
    live_allow_short: bool = False  # short-side live orders need a manual price-convention check

    @classmethod
    def from_env(cls, dotenv_path: str | Path | None = ".env") -> Settings:
        if dotenv_path and Path(dotenv_path).exists():
            load_dotenv(dotenv_path, override=False)
        env = os.environ.get
        return cls(
            db_path=Path(env("EDGEBOT_DB_PATH", "data/edgebot.db")),
            cache_dir=Path(env("EDGEBOT_CACHE_DIR", "cache")),
            log_level=env("EDGEBOT_LOG_LEVEL", "INFO").upper(),
            odds_api_key=Secret(env("ODDS_API_KEY")),
            anthropic_api_key=Secret(env("ANTHROPIC_API_KEY")),
            polymarket_key_id=Secret(env("POLYMARKET_KEY_ID")),
            polymarket_secret_key=Secret(env("POLYMARKET_SECRET_KEY")),
            discord_webhook_url=Secret(env("DISCORD_WEBHOOK_URL")),
            live_trading=_flag("LIVE_TRADING"),
            paper_bankroll=float(env("PAPER_BANKROLL", "1000")),
            live_bankroll_cap=float(env("LIVE_BANKROLL_CAP", "0")),
            live_allow_short=_flag("LIVE_ALLOW_SHORT"),
        )

    def secrets(self) -> list[str]:
        return [
            s.get()
            for s in (
                self.odds_api_key,
                self.anthropic_api_key,
                self.polymarket_key_id,
                self.polymarket_secret_key,
                self.discord_webhook_url,
            )
            if s
        ]


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        # longest first so a secret containing another is fully masked
        self._secrets = sorted({s for s in secrets if len(s) >= 6}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        for s in self._secrets:
            msg = msg.replace(s, "***")
        record.msg, record.args = msg, None
        return True


def install_log_redaction(settings: Settings) -> None:
    flt = RedactingFilter(settings.secrets())
    root = logging.getLogger()
    for handler in root.handlers or [logging.lastResort]:
        if handler is not None:
            handler.addFilter(flt)
