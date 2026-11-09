"""Import free historical closing lines for the backtest's market baseline.

Two layouts are supported:

* ``wide``: one row per game. Column names are mapped explicitly, so any
  free CSV (e.g. a Kaggle export) can be loaded without editing code.
* ``sbr``: the classic sportsbook-archive layout most free NBA odds sets come
  from: two rows per game (visitor then home), ``Date`` as MMDD, a ``VH``
  column and a ``ML`` (American) column.

Archive closing lines have no timestamp. A closing line is by definition the
last price before tip, so it is stamped ``known_at`` = the game date at 7pm ET,
the backtest's assumed decision time. The backtest decides at that same
instant, so the line is never visible to a decision made earlier.

Bad rows are rejected with a reason, never coerced. An import aborts if more
than ``max_reject_rate`` of rows fail, since that usually means a wrong
column mapping.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from edgebot.devig import american_to_decimal
from edgebot.teams import UnknownTeamError, resolve
from edgebot.timeutil import et_date_to_utc

ASSUMED_TIP_ET = time(19, 0)


@dataclass
class ColumnMap:
    date: str = "date"
    home: str = "home"
    away: str = "away"
    home_odds: str = "home_ml"
    away_odds: str = "away_ml"
    odds_format: str = "american"  # or "decimal"
    date_format: str | None = None  # strftime pattern; None = ISO / pandas inference


@dataclass
class ImportReport:
    rows: list[dict[str, Any]] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return len(self.rows) + sum(self.rejected.values())

    @property
    def reject_rate(self) -> float:
        return 0.0 if self.total == 0 else sum(self.rejected.values()) / self.total


class ImportAbortedError(ValueError):
    pass


def _to_decimal(value: Any, fmt: str) -> float:
    x = float(value)
    if fmt == "american":
        return american_to_decimal(x)
    if fmt == "decimal":
        if x <= 1:
            raise ValueError(f"decimal odds must be > 1: {x}")
        return x
    raise ValueError(f"unknown odds format: {fmt}")


def _row(game_day: date, home: str, away: str, home_dec: float, away_dec: float, source: str):
    if home == away:
        raise ValueError("home == away")
    return {
        "known_at": et_date_to_utc(game_day, ASSUMED_TIP_ET),
        "source": f"archive:{source}",
        "bookmaker": "consensus",
        "kind": "closing",
        "game_date": game_day.isoformat(),
        "home": home,
        "away": away,
        "home_dec": home_dec,
        "away_dec": away_dec,
    }


def _parse_day(raw: str, fmt: str | None) -> date:
    if fmt is None:
        return date.fromisoformat(raw[:10])
    return datetime.strptime(raw, fmt).date()  # noqa: DTZ007 - calendar date, no instant


def import_wide(df: pd.DataFrame, cmap: ColumnMap, source: str) -> ImportReport:
    missing = {cmap.date, cmap.home, cmap.away, cmap.home_odds, cmap.away_odds} - set(df.columns)
    if missing:
        raise ImportAbortedError(f"missing columns: {sorted(missing)}")
    rep = ImportReport()
    for rec in df.to_dict("records"):
        try:
            raw_date = str(rec[cmap.date]).strip()
            game_day = _parse_day(raw_date, cmap.date_format)
            rep.rows.append(
                _row(
                    game_day,
                    resolve(str(rec[cmap.home])),
                    resolve(str(rec[cmap.away])),
                    _to_decimal(rec[cmap.home_odds], cmap.odds_format),
                    _to_decimal(rec[cmap.away_odds], cmap.odds_format),
                    source,
                )
            )
        except UnknownTeamError:
            rep.rejected["unknown team"] += 1
        except (ValueError, TypeError):
            rep.rejected["bad date or odds"] += 1
    return rep


def import_sbr(df: pd.DataFrame, season_start_year: int, source: str) -> ImportReport:
    """SBR layout: consecutive (V, H) row pairs; 'N' rows are neutral-site."""
    need = {"Date", "VH", "Team", "ML"}
    if need - set(df.columns):
        raise ImportAbortedError(f"missing columns: {sorted(need - set(df.columns))}")
    rep = ImportReport()
    records = df.to_dict("records")
    if len(records) % 2:
        rep.rejected["unpaired trailing row"] += 1
        records = records[:-1]
    for vis, hom in zip(records[::2], records[1::2], strict=True):
        try:
            if (str(vis["VH"]), str(hom["VH"])) not in {("V", "H"), ("N", "N")}:
                raise ValueError("rows not in visitor/home order")
            if str(vis["Date"]) != str(hom["Date"]):
                raise ValueError("pair dates differ")
            mmdd = int(float(vis["Date"]))
            month, day = divmod(mmdd, 100)
            year = season_start_year if month >= 8 else season_start_year + 1
            rep.rows.append(
                _row(
                    date(year, month, day),
                    resolve(str(hom["Team"])),
                    resolve(str(vis["Team"])),
                    _to_decimal(hom["ML"], "american"),
                    _to_decimal(vis["ML"], "american"),
                    source,
                )
            )
        except UnknownTeamError:
            rep.rejected["unknown team"] += 1
        except (ValueError, TypeError):
            rep.rejected["bad pair, date or odds"] += 1
    return rep


def load_file(
    path: str | Path,
    *,
    layout: str,
    source: str,
    cmap: ColumnMap | None = None,
    season_start_year: int | None = None,
    max_reject_rate: float = 0.05,
) -> ImportReport:
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ImportAbortedError("only .csv is supported; export spreadsheets to CSV first")
    df = pd.read_csv(path)
    if layout == "wide":
        rep = import_wide(df, cmap or ColumnMap(), source)
    elif layout == "sbr":
        if season_start_year is None:
            raise ImportAbortedError("sbr layout needs season_start_year (dates are MMDD)")
        rep = import_sbr(df, season_start_year, source)
    else:
        raise ImportAbortedError(f"unknown layout: {layout}")
    if rep.reject_rate > max_reject_rate:
        raise ImportAbortedError(
            f"{rep.reject_rate:.1%} of rows rejected ({dict(rep.rejected)}); "
            "check the column mapping"
        )
    return rep
