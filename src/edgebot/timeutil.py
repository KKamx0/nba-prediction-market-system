"""Timezone-aware time helpers.

Every record edgebot stores carries the UTC instant it became known. Naive
datetimes are rejected outright: a single naive timestamp can silently shift a
feature across tip-off and leak the future into a backtest.

Stored timestamps use one fixed ISO-8601 format (UTC, microseconds, ``+00:00``)
so that string order in SQLite equals chronological order.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt


def to_iso(dt: datetime) -> str:
    """Serialize an aware datetime to the canonical sortable UTC string."""
    return require_aware(dt).astimezone(UTC).isoformat(timespec="microseconds")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string that includes an offset (``Z`` accepted)."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return require_aware(dt).astimezone(UTC)


def et_date_to_utc(d: date, at: time) -> datetime:
    """Wall-clock time on an Eastern-time calendar date, as UTC."""
    return datetime.combine(d, at, tzinfo=ET).astimezone(UTC)


def box_score_known_at(game_date_et: date) -> datetime:
    """Conservative instant a game's final box score is treated as known.

    NBA game logs give a date, not an end time. The latest regular tip is about
    10:30pm ET and games run ~2.5h, so 08:00 UTC the next day (3-4am ET) is
    safely after every final. Using the result any earlier would be leakage.
    """
    return datetime.combine(game_date_et + timedelta(days=1), time(8, 0), tzinfo=UTC)


def season_for_date(d: date) -> str:
    """NBA season label for a date, e.g. 2026-11-09 -> '2026-27'."""
    start_year = d.year if d.month >= 8 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"
