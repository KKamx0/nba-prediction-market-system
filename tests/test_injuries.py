from datetime import UTC, datetime

import pytest

from edgebot.db import Store
from edgebot.ingest.injuries import (
    PUBLISH_LAG,
    find_report_links,
    ingest_new_reports,
    parse_report_lines,
    report_time_from_url,
)
from edgebot.names import last_first_to_full, name_key

REPORT_TIME = datetime(2026, 11, 9, 22, 0, tzinfo=UTC)  # 5:00pm ET

# Text as pdfplumber extracts it: grouping columns only on a group's first row.
LINES = [
    "Injury Report: 11/09/26 05:00 PM",
    "Game Date Game Time Matchup Team Player Name Current Status Reason",
    "11/09/2026 07:00 (ET) LAL@ATL Atlanta Hawks Young, Trae Out Injury/Illness - Right MCL; Sprain",
    "Johnson, Jalen Questionable Injury/Illness - Left Ankle;",
    "Sprain",
    "Los Angeles Lakers James, LeBron Probable Injury/Illness - Left Foot; Arthritis",
    "Hachimura, Rui Available Injury/Illness - Right Calf; Strain",
    "07:30 (ET) MEM@TOR Memphis Grizzlies Jackson Jr., Jaren Doubtful Injury/Illness - Left Knee",
    "Toronto Raptors NOT YET SUBMITTED",
    "LA Clippers Leonard, Kawhi Out Rest",
    "Page 1 of 3",
    "some garbage line that matches nothing",
]


def test_parse_carries_forward_team_and_date():
    res = parse_report_lines(LINES, REPORT_TIME, "test")
    got = [(r["team"], r["player_name"], r["status"]) for r in res.rows]
    assert got == [
        ("ATL", "Trae Young", "Out"),
        ("ATL", "Jalen Johnson", "Questionable"),
        ("LAL", "LeBron James", "Probable"),
        ("LAL", "Rui Hachimura", "Available"),
        ("MEM", "Jaren Jackson Jr.", "Doubtful"),
        ("LAC", "Kawhi Leonard", "Out"),
    ]
    assert all(r["game_date"] == "2026-11-09" for r in res.rows)
    assert res.rows[1]["reason"] == "Injury/Illness - Left Ankle; Sprain"  # wrapped line joined
    assert res.rows[5]["reason"] == "Rest"
    assert all(r["known_at"] == REPORT_TIME + PUBLISH_LAG for r in res.rows)
    assert res.unparsed["unrecognized line"] == 1


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://x/Injury-Report_2026-11-09_05_00PM.pdf",
            datetime(2026, 11, 9, 22, 0, tzinfo=UTC),
        ),
        ("https://x/Injury-Report_2026-11-09_05PM.pdf", datetime(2026, 11, 9, 22, 0, tzinfo=UTC)),
        (
            "https://x/Injury-Report_2026-11-09_12_30PM.pdf",
            datetime(2026, 11, 9, 17, 30, tzinfo=UTC),
        ),
        (
            "https://x/Injury-Report_2026-11-09_12_15AM.pdf",
            datetime(2026, 11, 9, 5, 15, tzinfo=UTC),
        ),
        ("https://x/not-a-report.pdf", None),
    ],
)
def test_report_time_from_url(url, expected):
    assert report_time_from_url(url) == expected


def test_find_links_dedupes_and_sorts():
    html = """
      <a href="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-09_05PM.pdf">5pm</a>
      <a href='/referee/injury/Injury-Report_2026-11-09_01_30PM.pdf'>1:30</a>
      <a href="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-09_05PM.pdf">dup</a>
      <a href="/other.pdf">x</a>
    """
    links = find_report_links(html, "https://official.nba.com/page/")
    assert [u.rsplit("_", 1)[-1] for _, u in links] == ["30PM.pdf", "05PM.pdf"]
    assert (
        links[0][1]
        == "https://official.nba.com/referee/injury/Injury-Report_2026-11-09_01_30PM.pdf"
    )


class FakeReports:
    def __init__(self):
        self.fetched = []

    def links(self):
        return [
            (datetime(2026, 11, 9, 18, 30, tzinfo=UTC), "a"),
            (REPORT_TIME, "b"),
        ]

    def fetch(self, url, ts):
        self.fetched.append(url)
        return parse_report_lines(LINES, ts, url)


def test_ingest_only_fetches_new_reports():
    store = Store(":memory:")
    fake = FakeReports()
    assert ingest_new_reports(store, fake) == 12
    assert fake.fetched == ["a", "b"]
    fake.fetched.clear()
    assert ingest_new_reports(store, fake) == 0
    assert fake.fetched == []  # nothing newer than what is stored


@pytest.mark.parametrize(
    "raw,full",
    [
        ("Jackson Jr., Jaren", "Jaren Jackson Jr."),
        ("James, LeBron", "LeBron James"),
        ("Nikola Jokic", "Nikola Jokic"),
    ],
)
def test_last_first_to_full(raw, full):
    assert last_first_to_full(raw) == full


def test_name_key_matches_across_sources():
    assert name_key("Jaren Jackson Jr.") == name_key("Jaren Jackson") == "jaren jackson"
    assert name_key("Nikola Jokić") == name_key("Nikola Jokic")
    assert name_key("Shai Gilgeous-Alexander") == "shai gilgeous alexander"
