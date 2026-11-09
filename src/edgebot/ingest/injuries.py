"""Official NBA injury report: discovery, download, and parsing.

Reports are PDFs published several times a day. Rather than guessing file
names, the listing page is scraped for PDF links and each report's time is
read from its file name (Eastern time), e.g.
``Injury-Report_2026-11-09_05_00PM.pdf`` or ``Injury-Report_2026-11-09_05PM.pdf``.

``known_at`` = report time + ``PUBLISH_LAG``: a report stamped 5:00pm is not
online at exactly 5:00pm, and claiming it was would leak a few minutes.

Parsing works on extracted text lines. The PDF prints date, time, matchup and
team only on the first row of each group, so those carry forward. Rows that
don't parse are counted, not guessed.
"""

from __future__ import annotations

import io
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import urljoin

from edgebot.ingest.http import JsonClient
from edgebot.names import last_first_to_full
from edgebot.teams import TEAMS
from edgebot.timeutil import et_date_to_utc, parse_iso, utc_now

log = logging.getLogger(__name__)

LISTING_URL = "https://official.nba.com/nba-injury-report-2026-27-season/"
PUBLISH_LAG = timedelta(minutes=15)
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")

_PDF_RE = re.compile(
    r"Injury-Report_(?P<date>\d{4}-\d{2}-\d{2})_(?P<hh>\d{2})(?:_(?P<mm>\d{2}))?(?P<ampm>AM|PM)\.pdf"
)
_LINK_RE = re.compile(r"""href=["']([^"']*Injury-Report_[^"']+\.pdf)["']""")
_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
_ROW_RE = re.compile(
    r"(?P<player>[A-Z][A-Za-z'.\- ]*(?: (?:Jr|Sr|II|III|IV)\.?)?, [A-Z][A-Za-z'.\- ]*?)"
    rf" (?P<status>{'|'.join(STATUSES)})(?: (?P<reason>.+))?$"
)
_SKIP_RE = re.compile(r"^(Injury Report:|Page \d+ of \d+|Game Date|Game Time)")

# Team names as they appear in the report, longest first so "LA Clippers"
# never matches inside another name.
_TEAM_NAMES = sorted(((t.name, t.abbr) for t in TEAMS.values()), key=lambda x: -len(x[0]))


def report_time_from_url(url: str) -> datetime | None:
    m = _PDF_RE.search(url)
    if not m:
        return None
    hh = int(m["hh"]) % 12 + (12 if m["ampm"] == "PM" else 0)
    mm = int(m["mm"] or 0)
    return et_date_to_utc(date.fromisoformat(m["date"]), time(hh, mm))


def find_report_links(listing_html: str, base_url: str = LISTING_URL) -> list[tuple[datetime, str]]:
    """All (report_time_utc, absolute_url) pairs on the listing page, oldest first."""
    out = {}
    for href in _LINK_RE.findall(listing_html):
        url = urljoin(base_url, href)
        ts = report_time_from_url(url)
        if ts is not None:
            out[url] = ts
    return sorted(((ts, url) for url, ts in out.items()), key=lambda x: x[0])


@dataclass
class ParseResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    unparsed: Counter = field(default_factory=Counter)


def _continues(prev: dict[str, Any] | None, line: str, has_date: bool) -> bool:
    """Only join a line to the previous reason if that reason visibly broke mid-phrase."""
    if prev is None or has_date or not prev["reason"]:
        return False
    if any(s in line.split() for s in STATUSES):
        return False
    return prev["reason"].rstrip()[-1] in ";-,/"


def parse_report_lines(lines: list[str], report_time: datetime, source: str) -> ParseResult:
    res = ParseResult()
    team: str | None = None
    game_date: str | None = None
    last_row: dict[str, Any] | None = None
    for raw in lines:
        line = " ".join(raw.split())
        if not line or _SKIP_RE.match(line):
            last_row = None
            continue
        d = _DATE_RE.search(line)
        if d:
            game_date = f"{d[3]}-{d[1]}-{d[2]}"
        for full, abbr in _TEAM_NAMES:
            if full in line:
                team = abbr
                line = line.split(full, 1)[1].strip()
                break
        if "NOT YET SUBMITTED" in line:
            last_row = None
            continue
        m = _ROW_RE.search(line)
        if m and team:
            last_row = {
                "known_at": report_time + PUBLISH_LAG,
                "game_date": game_date,
                "team": team,
                "player_name": last_first_to_full(m["player"].strip()),
                "status": m["status"],
                "reason": (m["reason"] or "").strip() or None,
                "source": source,
            }
            res.rows.append(last_row)
        elif _continues(last_row, line, bool(d)):
            # the PDF wrapped a long reason onto the next line
            last_row["reason"] = f"{last_row['reason']} {line}"
        else:
            res.unparsed["unrecognized line"] += 1
            last_row = None
    return res


def pdf_to_lines(pdf_bytes: bytes) -> list[str]:
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").splitlines())
    return lines


class InjuryReports:
    def __init__(self, client: JsonClient | None = None, listing_url: str = LISTING_URL):
        self._client = client or JsonClient("https://official.nba.com", rate_per_s=0.5)
        self.listing_url = listing_url

    def links(self) -> list[tuple[datetime, str]]:
        return find_report_links(self._client.get_text(self.listing_url), self.listing_url)

    def fetch(self, url: str, report_time: datetime) -> ParseResult:
        result = parse_report_lines(pdf_to_lines(self._client.get_bytes(url)), report_time, url)
        if result.unparsed:
            log.warning("%s: %d unparsed lines", url, sum(result.unparsed.values()))
        return result


def ingest_new_reports(
    store,
    reports: InjuryReports,
    since: datetime | None = None,
    *,
    now: datetime | None = None,
    backfill: timedelta = timedelta(hours=36),
) -> int:
    """Fetch listed reports newer than ``since``.

    By default ``since`` is the newest stored report, or ``now - backfill`` on an
    empty database, so a first run doesn't download a whole season of PDFs.
    Pass an explicit ``since`` to backfill history deliberately.
    """
    if since is None:
        latest = store.query("SELECT MAX(known_at) AS t FROM injury_reports")[0]["t"]
        since = parse_iso(latest) - PUBLISH_LAG if latest else (now or utc_now()) - backfill
    inserted = 0
    for ts, url in reports.links():
        if ts <= since:
            continue
        inserted += store.insert_injury_rows(reports.fetch(url, ts).rows)
    return inserted
