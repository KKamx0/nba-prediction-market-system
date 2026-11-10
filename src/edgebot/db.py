"""SQLite storage with point-in-time ("as of") reads.

Rules this module enforces:

* Every row carries ``known_at``: the UTC instant edgebot could first have
  known it. Timestamps are written through ``to_iso`` so naive datetimes fail.
* Feature code reads through the ``*_as_of`` helpers, which only return rows
  with ``known_at <= as_of``. That single comparison is what keeps backtests
  honest; the leakage test checks it end to end.
* Duplicate rows are skipped with ``ON CONFLICT DO NOTHING``. ``INSERT OR
  IGNORE`` is never used: it would also silently drop rows that fail a CHECK
  constraint, hiding bad data instead of raising.
* Schema changes are append-only migrations, so later phases add tables
  without touching earlier ones.

The database file lives under ``data/`` and is gitignored.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from edgebot.timeutil import to_iso

MIGRATIONS: list[str] = [
    # 1: ingestion tables
    """
    CREATE TABLE games (
        game_id    TEXT PRIMARY KEY,
        season     TEXT NOT NULL,
        game_date  TEXT NOT NULL,              -- Eastern-time calendar date
        tip_utc    TEXT,                       -- scheduled tip, if known
        home       TEXT NOT NULL,
        away       TEXT NOT NULL,
        known_at   TEXT NOT NULL,
        CHECK (home <> away)
    );
    CREATE INDEX games_date ON games (game_date);

    CREATE TABLE game_results (
        game_id    TEXT PRIMARY KEY REFERENCES games (game_id),
        home_pts   INTEGER NOT NULL,
        away_pts   INTEGER NOT NULL,
        known_at   TEXT NOT NULL,
        CHECK (home_pts <> away_pts)
    );

    CREATE TABLE player_games (
        game_id     TEXT NOT NULL REFERENCES games (game_id),
        player_id   INTEGER NOT NULL,
        player_name TEXT NOT NULL,
        team        TEXT NOT NULL,
        minutes     REAL NOT NULL CHECK (minutes >= 0 AND minutes <= 70),
        plus_minus  REAL,
        pts         INTEGER,
        started     INTEGER,
        known_at    TEXT NOT NULL,
        PRIMARY KEY (game_id, player_id)
    );
    CREATE INDEX player_games_player ON player_games (player_id, known_at);

    CREATE TABLE injury_reports (
        id           INTEGER PRIMARY KEY,
        known_at     TEXT NOT NULL,            -- report publication time
        game_date    TEXT,
        team         TEXT NOT NULL,
        player_name  TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN
                        ('Out', 'Doubtful', 'Questionable', 'Probable', 'Available')),
        reason       TEXT,
        source       TEXT NOT NULL,
        UNIQUE (known_at, team, player_name)
    );
    CREATE INDEX injury_reports_lookup ON injury_reports (team, player_name, known_at);

    CREATE TABLE market_snapshots (
        id           INTEGER PRIMARY KEY,
        known_at     TEXT NOT NULL,
        market_slug  TEXT NOT NULL,
        event_slug   TEXT,
        game_date    TEXT,
        home         TEXT,
        away         TEXT,
        long_team    TEXT,                     -- team whose win pays the YES side
        state        TEXT,
        best_bid     REAL CHECK (best_bid IS NULL OR (best_bid >= 0 AND best_bid <= 1)),
        best_ask     REAL CHECK (best_ask IS NULL OR (best_ask >= 0 AND best_ask <= 1)),
        last_trade   REAL,
        bids_json    TEXT NOT NULL,            -- [[price, qty], ...] best first
        asks_json    TEXT NOT NULL
    );
    CREATE INDEX market_snapshots_slug ON market_snapshots (market_slug, known_at);

    CREATE TABLE odds_quotes (
        id          INTEGER PRIMARY KEY,
        known_at    TEXT NOT NULL,
        source      TEXT NOT NULL,             -- 'odds_api', 'archive:<name>', ...
        bookmaker   TEXT NOT NULL,             -- 'consensus' if the source is an average
        kind        TEXT NOT NULL CHECK (kind IN ('live', 'opening', 'closing')),
        game_date   TEXT NOT NULL,
        home        TEXT NOT NULL,
        away        TEXT NOT NULL,
        home_dec    REAL NOT NULL CHECK (home_dec > 1),
        away_dec    REAL NOT NULL CHECK (away_dec > 1),
        UNIQUE (source, bookmaker, kind, game_date, home, away, known_at)
    );
    CREATE INDEX odds_quotes_game ON odds_quotes (game_date, home, away, known_at);
    """,
    # 2: team box totals, for possessions (pace) and offense/defense splits
    """
    CREATE TABLE team_games (
        game_id   TEXT NOT NULL REFERENCES games (game_id),
        team      TEXT NOT NULL,
        opp       TEXT NOT NULL,
        is_home   INTEGER NOT NULL CHECK (is_home IN (0, 1)),
        pts       INTEGER NOT NULL,
        fga       INTEGER NOT NULL,
        fta       INTEGER NOT NULL,
        oreb      INTEGER NOT NULL,
        tov       INTEGER NOT NULL,
        poss      REAL NOT NULL CHECK (poss > 50 AND poss < 160),
        known_at  TEXT NOT NULL,
        PRIMARY KEY (game_id, team)
    );
    CREATE INDEX team_games_team ON team_games (team, known_at);
    """,
    # 3: model inputs and outputs
    """
    CREATE TABLE features (
        id          INTEGER PRIMARY KEY,
        game_date   TEXT NOT NULL,
        home        TEXT NOT NULL,
        away        TEXT NOT NULL,
        as_of       TEXT NOT NULL,             -- decision time the vector was built for
        version     TEXT NOT NULL,
        values_json TEXT NOT NULL,
        known_at    TEXT NOT NULL,             -- when it was computed
        UNIQUE (game_date, home, away, as_of, version)
    );

    CREATE TABLE predictions (
        id           INTEGER PRIMARY KEY,
        game_date    TEXT NOT NULL,
        home         TEXT NOT NULL,
        away         TEXT NOT NULL,
        as_of        TEXT NOT NULL,
        model        TEXT NOT NULL,
        p_home       REAL NOT NULL CHECK (p_home > 0 AND p_home < 1),
        p_home_play  REAL,                     -- every uncertain player plays
        p_home_sit   REAL,                     -- every uncertain player sits
        p_market     REAL,                     -- de-vigged sportsbook consensus
        known_at     TEXT NOT NULL
    );
    CREATE INDEX predictions_game ON predictions (game_date, home, away, as_of);
    """,
    # 4: trading log. Every decision is recorded, including passes and why.
    """
    CREATE TABLE decisions (
        id            INTEGER PRIMARY KEY,
        known_at      TEXT NOT NULL,
        game_date     TEXT NOT NULL,
        home          TEXT NOT NULL,
        away          TEXT NOT NULL,
        market_slug   TEXT,
        model         TEXT,
        action        TEXT NOT NULL CHECK (action IN ('bet', 'pass', 'review')),
        side_team     TEXT,
        p_model       REAL,
        p_play        REAL,
        p_sit         REAL,
        p_market_pm   REAL,                     -- Polymarket mid for the side
        p_market_sb   REAL,                     -- sportsbook consensus for the side
        edge          REAL,
        all_in_price  REAL,
        contracts     REAL,
        stake         REAL,
        reasons_json  TEXT NOT NULL
    );
    CREATE INDEX decisions_game ON decisions (game_date, home, away, known_at);

    CREATE TABLE orders (
        id                 INTEGER PRIMARY KEY,
        known_at           TEXT NOT NULL,
        decision_id        INTEGER REFERENCES decisions (id),
        mode               TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
        market_slug        TEXT NOT NULL,
        side_team          TEXT NOT NULL,
        long_side          INTEGER NOT NULL,
        qty                REAL NOT NULL CHECK (qty > 0),
        limit_price        REAL NOT NULL CHECK (limit_price > 0 AND limit_price < 1),
        status             TEXT NOT NULL,
        exchange_order_id  TEXT
    );

    CREATE TABLE fills (
        id        INTEGER PRIMARY KEY,
        order_id  INTEGER NOT NULL REFERENCES orders (id),
        known_at  TEXT NOT NULL,
        price     REAL NOT NULL CHECK (price > 0 AND price < 1),
        qty       REAL NOT NULL CHECK (qty > 0),
        fee       REAL NOT NULL CHECK (fee >= 0)
    );

    CREATE TABLE positions (
        id             INTEGER PRIMARY KEY,
        order_id       INTEGER NOT NULL REFERENCES orders (id),
        mode           TEXT NOT NULL,
        market_slug    TEXT NOT NULL,
        game_date      TEXT NOT NULL,
        home           TEXT NOT NULL,
        away           TEXT NOT NULL,
        side_team      TEXT NOT NULL,
        long_side      INTEGER NOT NULL,
        tip_utc        TEXT,
        qty            REAL NOT NULL,
        cost           REAL NOT NULL,           -- contracts x price, excluding fees
        fees           REAL NOT NULL,
        opened_at      TEXT NOT NULL,
        closing_price  REAL,                    -- side mid in the last snapshot before tip
        settled_at     TEXT,
        won            INTEGER,
        payout         REAL,
        pnl            REAL
    );
    CREATE INDEX positions_open ON positions (settled_at);

    CREATE TABLE risk_state (
        id          INTEGER PRIMARY KEY CHECK (id = 1),
        state_json  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    CREATE TABLE risk_events (
        id        INTEGER PRIMARY KEY,
        known_at  TEXT NOT NULL,
        kind      TEXT NOT NULL,
        detail    TEXT NOT NULL
    );
    """,
    # 5: news articles and the LLM's structured extractions from them
    """
    CREATE TABLE news_articles (
        url           TEXT PRIMARY KEY,
        feed          TEXT NOT NULL,
        title         TEXT NOT NULL,
        published_at  TEXT NOT NULL,
        fetched_at    TEXT NOT NULL,
        status        TEXT NOT NULL,           -- 'extracted' | 'skipped' | 'rejected' | 'error'
        detail        TEXT
    );

    CREATE TABLE news_extractions (
        id                       INTEGER PRIMARY KEY,
        known_at                 TEXT NOT NULL,   -- max(published_at, extraction time)
        source_url               TEXT NOT NULL REFERENCES news_articles (url),
        published_at             TEXT NOT NULL,
        player                   TEXT NOT NULL,
        team                     TEXT NOT NULL,
        status                   TEXT NOT NULL CHECK (status IN
                                    ('out', 'doubtful', 'questionable', 'probable',
                                     'available', 'minutes_limit')),
        minutes_impact_estimate  REAL NOT NULL CHECK (minutes_impact_estimate BETWEEN -48 AND 24),
        confidence               REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        model                    TEXT NOT NULL
    );
    CREATE INDEX news_extractions_team ON news_extractions (team, known_at);
    """,
    # 6: separate risk state per mode, so a live drawdown never mixes with paper P&L
    """
    CREATE TABLE risk_states (
        mode        TEXT PRIMARY KEY CHECK (mode IN ('paper', 'live')),
        state_json  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    INSERT INTO risk_states (mode, state_json, updated_at)
        SELECT 'paper', state_json, updated_at FROM risk_state;
    DROP TABLE risk_state;
    """,
    # 7: self-learning: what we projected, how each bet went, what was learned
    """
    CREATE TABLE minutes_projections (
        id           INTEGER PRIMARY KEY,
        known_at     TEXT NOT NULL,             -- decision time the projection was made
        game_date    TEXT NOT NULL,
        team         TEXT NOT NULL,
        player_id    INTEGER NOT NULL,
        player_name  TEXT NOT NULL,
        projected    REAL NOT NULL CHECK (projected >= 0 AND projected <= 48),
        status       TEXT,
        UNIQUE (game_date, team, player_id, known_at)
    );
    CREATE INDEX minutes_projections_game ON minutes_projections (game_date, team);

    CREATE TABLE bet_reviews (
        position_id  INTEGER PRIMARY KEY REFERENCES positions (id),
        known_at     TEXT NOT NULL,
        verdict      TEXT NOT NULL,
        clv          REAL,
        surprises    TEXT NOT NULL,             -- JSON list of availability misses
        notes        TEXT NOT NULL
    );

    CREATE TABLE learned_rules (
        id         INTEGER PRIMARY KEY,
        known_at   TEXT NOT NULL,
        kind       TEXT NOT NULL,               -- 'segment' | 'minutes_bias'
        dimension  TEXT NOT NULL,
        value      TEXT NOT NULL,
        n          INTEGER NOT NULL,
        estimate   REAL NOT NULL,
        lower      REAL,
        upper      REAL,
        action     TEXT NOT NULL
    );
    CREATE INDEX learned_rules_time ON learned_rules (kind, known_at);

    CREATE TABLE model_versions (
        id                 INTEGER PRIMARY KEY,
        known_at           TEXT NOT NULL,
        model              TEXT NOT NULL,
        train_end          TEXT NOT NULL,
        n_evaluated        INTEGER NOT NULL,
        champion_log_loss  REAL,
        challenger_log_loss REAL,
        promoted           INTEGER NOT NULL,
        reason             TEXT NOT NULL
    );
    """,
]


class Store:
    """Thin wrapper around a SQLite connection."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    # -- schema --------------------------------------------------------------
    def schema_version(self) -> int:
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] or 0

    def migrate(self) -> None:
        current = self.schema_version()
        for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            with self.conn:
                self.conn.executescript(sql)
                self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    def close(self) -> None:
        self.conn.close()

    # -- generic -------------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    # -- writes --------------------------------------------------------------
    def upsert_game(
        self,
        *,
        game_id: str,
        season: str,
        game_date: str,
        home: str,
        away: str,
        known_at: datetime,
        tip_utc: datetime | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO games (game_id, season, game_date, tip_utc, home, away, known_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (game_id) DO UPDATE SET
                    tip_utc = COALESCE(excluded.tip_utc, games.tip_utc)
                """,
                (
                    game_id,
                    season,
                    game_date,
                    to_iso(tip_utc) if tip_utc else None,
                    home,
                    away,
                    to_iso(known_at),
                ),
            )

    def upsert_result(
        self, *, game_id: str, home_pts: int, away_pts: int, known_at: datetime
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO game_results (game_id, home_pts, away_pts, known_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (game_id) DO NOTHING
                """,
                (game_id, home_pts, away_pts, to_iso(known_at)),
            )

    def upsert_team_games(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (
                r["game_id"],
                r["team"],
                r["opp"],
                int(r["is_home"]),
                int(r["pts"]),
                int(r["fga"]),
                int(r["fta"]),
                int(r["oreb"]),
                int(r["tov"]),
                float(r["poss"]),
                to_iso(r["known_at"]),
            )
            for r in rows
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO team_games (game_id, team, opp, is_home, pts, fga, fta, oreb,
                                        tov, poss, known_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (game_id, team) DO NOTHING
                """,
                payload,
            )
        return len(payload)

    def team_games_as_of(self, as_of: datetime) -> list[sqlite3.Row]:
        return self.query(
            """
            SELECT tg.*, g.game_date, g.season FROM team_games tg JOIN games g USING (game_id)
            WHERE tg.known_at <= ? ORDER BY g.game_date, tg.game_id, tg.team
            """,
            (to_iso(as_of),),
        )

    def upsert_player_games(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (
                r["game_id"],
                int(r["player_id"]),
                r["player_name"],
                r["team"],
                float(r["minutes"]),
                None if r.get("plus_minus") is None else float(r["plus_minus"]),
                None if r.get("pts") is None else int(r["pts"]),
                None if r.get("started") is None else int(bool(r["started"])),
                to_iso(r["known_at"]),
            )
            for r in rows
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO player_games (game_id, player_id, player_name, team, minutes,
                                          plus_minus, pts, started, known_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (game_id, player_id) DO NOTHING
                """,
                payload,
            )
        return len(payload)

    def insert_injury_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (
                to_iso(r["known_at"]),
                r.get("game_date"),
                r["team"],
                r["player_name"],
                r["status"],
                r.get("reason"),
                r["source"],
            )
            for r in rows
        ]
        with self.conn:
            cur = self.conn.executemany(
                """
                INSERT INTO injury_reports
                    (known_at, game_date, team, player_name, status, reason, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                payload,
            )
        return cur.rowcount

    def insert_market_snapshot(
        self,
        *,
        known_at: datetime,
        market_slug: str,
        bids: Sequence[tuple[float, float]],
        asks: Sequence[tuple[float, float]],
        event_slug: str | None = None,
        game_date: str | None = None,
        home: str | None = None,
        away: str | None = None,
        long_team: str | None = None,
        state: str | None = None,
        last_trade: float | None = None,
    ) -> None:
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO market_snapshots
                    (known_at, market_slug, event_slug, game_date, home, away, long_team,
                     state, best_bid, best_ask, last_trade, bids_json, asks_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    to_iso(known_at),
                    market_slug,
                    event_slug,
                    game_date,
                    home,
                    away,
                    long_team,
                    state,
                    best_bid,
                    best_ask,
                    last_trade,
                    json.dumps([list(level) for level in bids]),
                    json.dumps([list(level) for level in asks]),
                ),
            )

    def insert_odds_quotes(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (
                to_iso(r["known_at"]),
                r["source"],
                r.get("bookmaker", "consensus"),
                r["kind"],
                r["game_date"],
                r["home"],
                r["away"],
                float(r["home_dec"]),
                float(r["away_dec"]),
            )
            for r in rows
        ]
        with self.conn:
            cur = self.conn.executemany(
                """
                INSERT INTO odds_quotes
                    (known_at, source, bookmaker, kind, game_date, home, away,
                     home_dec, away_dec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                payload,
            )
        return cur.rowcount

    def save_features(
        self,
        *,
        game_date: str,
        home: str,
        away: str,
        as_of: datetime,
        version: str,
        values: dict[str, Any],
        known_at: datetime,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO features (game_date, home, away, as_of, version, values_json, known_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (game_date, home, away, as_of, version)
                DO UPDATE SET values_json = excluded.values_json, known_at = excluded.known_at
                """,
                (
                    game_date,
                    home,
                    away,
                    to_iso(as_of),
                    version,
                    json.dumps(values, sort_keys=True),
                    to_iso(known_at),
                ),
            )

    def save_prediction(self, **row: Any) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO predictions (game_date, home, away, as_of, model, p_home,
                                         p_home_play, p_home_sit, p_market, known_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["game_date"],
                    row["home"],
                    row["away"],
                    to_iso(row["as_of"]),
                    row["model"],
                    row["p_home"],
                    row.get("p_home_play"),
                    row.get("p_home_sit"),
                    row.get("p_market"),
                    to_iso(row["known_at"]),
                ),
            )

    # -- point-in-time reads -----------------------------------------------
    def player_games_as_of(self, as_of: datetime) -> list[sqlite3.Row]:
        """Every player box-score line known at ``as_of``."""
        return self.query(
            "SELECT * FROM player_games WHERE known_at <= ? ORDER BY known_at, game_id",
            (to_iso(as_of),),
        )

    def player_lines_as_of(self, as_of: datetime) -> list[sqlite3.Row]:
        """Box-score lines known at ``as_of``, with each game's date and season."""
        return self.query(
            """
            SELECT pg.*, g.game_date, g.season FROM player_games pg JOIN games g USING (game_id)
            WHERE pg.known_at <= ? ORDER BY g.game_date, pg.game_id
            """,
            (to_iso(as_of),),
        )

    def results_as_of(self, as_of: datetime) -> list[sqlite3.Row]:
        return self.query(
            """
            SELECT g.*, r.home_pts, r.away_pts, r.known_at AS result_known_at
            FROM game_results r JOIN games g USING (game_id)
            WHERE r.known_at <= ?
            ORDER BY g.game_date, g.game_id
            """,
            (to_iso(as_of),),
        )

    def injury_status_as_of(self, team: str, as_of: datetime) -> list[sqlite3.Row]:
        """Latest status per player for ``team`` from reports published by ``as_of``."""
        return self.query(
            """
            SELECT ir.* FROM injury_reports ir
            JOIN (
                SELECT player_name, MAX(known_at) AS latest
                FROM injury_reports
                WHERE team = ? AND known_at <= ?
                GROUP BY player_name
            ) m ON ir.player_name = m.player_name AND ir.known_at = m.latest
            WHERE ir.team = ?
            ORDER BY ir.player_name
            """,
            (team, to_iso(as_of), team),
        )

    def latest_market_snapshot_as_of(self, market_slug: str, as_of: datetime) -> sqlite3.Row | None:
        rows = self.query(
            """
            SELECT * FROM market_snapshots
            WHERE market_slug = ? AND known_at <= ?
            ORDER BY known_at DESC LIMIT 1
            """,
            (market_slug, to_iso(as_of)),
        )
        return rows[0] if rows else None

    def odds_as_of(
        self, *, game_date: str, home: str, away: str, as_of: datetime
    ) -> list[sqlite3.Row]:
        """Most recent quote per (source, bookmaker) for a game, known by ``as_of``."""
        return self.query(
            """
            SELECT q.* FROM odds_quotes q
            JOIN (
                SELECT source, bookmaker, MAX(known_at) AS latest
                FROM odds_quotes
                WHERE game_date = ? AND home = ? AND away = ? AND known_at <= ?
                GROUP BY source, bookmaker
            ) m ON q.source = m.source AND q.bookmaker = m.bookmaker
               AND q.known_at = m.latest
            WHERE q.game_date = ? AND q.home = ? AND q.away = ?
            """,
            (game_date, home, away, to_iso(as_of), game_date, home, away),
        )
