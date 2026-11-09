from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from edgebot.timeutil import (
    box_score_known_at,
    et_date_to_utc,
    parse_iso,
    require_aware,
    season_for_date,
    to_iso,
)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        require_aware(datetime(2026, 11, 9, 12, 0))  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ValueError):
        parse_iso("2026-11-09T12:00:00")


def test_iso_roundtrip_normalizes_to_utc():
    pst = timezone(timedelta(hours=-8))
    dt = datetime(2026, 11, 9, 16, 0, tzinfo=pst)
    s = to_iso(dt)
    assert s == "2026-11-10T00:00:00.000000+00:00"
    assert parse_iso(s) == dt
    assert parse_iso("2026-11-10T00:00:00Z") == dt


def test_iso_strings_sort_chronologically():
    a = datetime(2026, 1, 1, 9, 59, 59, 999999, tzinfo=UTC)
    b = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert to_iso(a) < to_iso(b)


def test_et_conversion_handles_dst():
    # November 9 is after DST ends: ET = UTC-5
    assert et_date_to_utc(date(2026, 11, 9), time(19, 0)) == datetime(
        2026, 11, 10, 0, 0, tzinfo=UTC
    )
    # April is DST: ET = UTC-4
    assert et_date_to_utc(date(2026, 4, 1), time(19, 0)) == datetime(2026, 4, 1, 23, 0, tzinfo=UTC)


def test_box_score_known_after_any_possible_final():
    known = box_score_known_at(date(2026, 11, 9))
    latest_final = et_date_to_utc(date(2026, 11, 9), time(23, 59))
    assert known > latest_final


@pytest.mark.parametrize(
    "d,expected",
    [
        (date(2026, 11, 9), "2026-27"),
        (date(2027, 4, 10), "2026-27"),
        (date(2026, 6, 15), "2025-26"),
        (date(1999, 12, 1), "1999-00"),
    ],
)
def test_season_label(d, expected):
    assert season_for_date(d) == expected
