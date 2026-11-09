"""Leakage tests: features must not change when post-decision data changes.

If any feature (now or in the future) reads data with known_at > as_of, the
vectors built before and after corrupting the future will differ and this
test fails CI.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import pytest

from edgebot.db import Store
from edgebot.model.features import MODEL_FEATURES, FeatureBuilder
from edgebot.timeutil import et_date_to_utc, to_iso
from tests.synthetic import load_into_store, make_league


@pytest.fixture
def loaded_store():
    store = Store(":memory:")
    load_into_store(store, make_league(n_games=1230, seed=7), "2025-26")
    yield store
    store.close()


def _sample_games(store, n_per_day=2):
    """Games from early, middle and late in the season, so history depth varies."""
    days = [r["game_date"] for r in store.query("SELECT DISTINCT game_date FROM games ORDER BY 1")]
    picks = [days[int(len(days) * q)] for q in (0.15, 0.5, 0.85)]
    out = []
    for d in picks:
        rows = store.query(
            "SELECT game_id, game_date, home, away FROM games WHERE game_date = ? LIMIT ?",
            (d, n_per_day),
        )
        out += [(r["game_id"], r["game_date"], r["home"], r["away"]) for r in rows]
    assert len(out) == 3 * n_per_day
    return out


def _clone(store):
    copy = Store(":memory:")
    store.conn.backup(copy.conn)
    return copy


def _decision_time(game_date: str) -> datetime:
    return et_date_to_utc(date.fromisoformat(game_date), time(19, 0))


def _vectors(store, games, builder_cls=FeatureBuilder):
    builder = builder_cls(store)
    out = {}
    for gid, gd, home, away in games:
        f = builder.build(game_date=gd, home=home, away=away, as_of=_decision_time(gd), save=False)
        out[gid] = {
            k: getattr(f, k) for k in [*MODEL_FEATURES, "strength_diff_play", "strength_diff_sit"]
        }
    return out


def _corrupt_the_future(store, games):
    """Change or add every kind of row that becomes known after the decision time.

    Callers pass games that share one decision time.
    """
    cutoff = to_iso(min(_decision_time(gd) for _, gd, _, _ in games))
    with store.conn:
        # results and box scores that were not yet known: wildly different numbers
        store.conn.execute("UPDATE team_games SET pts = pts + 60 WHERE known_at > ?", (cutoff,))
        store.conn.execute(
            "UPDATE game_results SET home_pts = home_pts + 60 WHERE known_at > ?", (cutoff,)
        )
        store.conn.execute(
            "UPDATE player_games SET minutes = minutes * 0.5, plus_minus = 40 WHERE known_at > ?",
            (cutoff,),
        )
    for _gid, gd, home, away in games:
        late = _decision_time(gd) + timedelta(minutes=1)
        # a late injury report ruling out every rostered home player
        names = [
            r["player_name"]
            for r in store.query(
                "SELECT DISTINCT player_name FROM player_games WHERE team = ?", (home,)
            )
        ]
        store.insert_injury_rows(
            {
                "known_at": late,
                "game_date": gd,
                "team": home,
                "player_name": n,
                "status": "Out",
                "source": "late",
            }
            for n in names
        )
        # a late news extraction claiming the home team's top player is out
        url = f"https://news.test/{gd}/{home}"
        with store.conn:
            store.conn.execute(
                "INSERT INTO news_articles (url, feed, title, published_at, fetched_at, status) "
                "VALUES (?, 'test', 't', ?, ?, 'extracted')",
                (url, to_iso(late), to_iso(late)),
            )
            for n in names[:3]:
                store.conn.execute(
                    "INSERT INTO news_extractions (known_at, source_url, published_at, player, team,"
                    " status, minutes_impact_estimate, confidence, model)"
                    " VALUES (?, ?, ?, ?, ?, 'out', -40, 0.99, 'test')",
                    (to_iso(late), url, to_iso(late), n, home),
                )
        # late odds moving the line to a near-lock
        store.insert_odds_quotes(
            [
                {
                    "known_at": late,
                    "source": "late-book",
                    "bookmaker": "x",
                    "kind": "live",
                    "game_date": gd,
                    "home": home,
                    "away": away,
                    "home_dec": 1.01,
                    "away_dec": 30.0,
                }
            ]
        )


def test_features_ignore_everything_after_decision_time(loaded_store):
    games = _sample_games(loaded_store)
    for game in games:
        store = _clone(loaded_store)
        try:
            before = _vectors(store, [game])
            n_future = store.query(
                "SELECT COUNT(*) AS c FROM team_games WHERE known_at > ?",
                (to_iso(_decision_time(game[1])),),
            )[0]["c"]
            assert n_future > 0, "nothing to corrupt: test would be vacuous"
            _corrupt_the_future(store, [game])
            after = _vectors(store, [game])
        finally:
            store.close()
        gid = game[0]
        assert after[gid] == pytest.approx(before[gid], nan_ok=True), f"leak in game {game}"


def test_corruption_does_move_features_when_the_cutoff_is_later(loaded_store):
    """Sanity check on the harness: corrupting *known* history must change features."""
    early, late = _sample_games(loaded_store)[0], _sample_games(loaded_store)[-1]
    store = _clone(loaded_store)
    try:
        before = _vectors(store, [late])
        _corrupt_the_future(store, [early])  # rows after the early cutoff are known by `late`
        after = _vectors(store, [late])
    finally:
        store.close()
    assert after[late[0]] != pytest.approx(before[late[0]], nan_ok=True)


class LeakyBuilder(FeatureBuilder):
    """Deliberately reads the game's final margin regardless of as_of."""

    def build(self, *, game_date, home, away, as_of, minutes_delta=None, save=True):
        f = super().build(game_date=game_date, home=home, away=away, as_of=as_of, save=False)
        r = self.store.query(
            "SELECT home_pts - away_pts AS m FROM game_results JOIN games USING (game_id) "
            "WHERE game_date = ? AND home = ? AND away = ?",
            (game_date, home, away),
        )
        return replace(f, pace=float(r[0]["m"]) if r else 0.0)


def test_the_leakage_check_catches_a_leaky_feature(loaded_store):
    game = _sample_games(loaded_store)[2]
    before = _vectors(loaded_store, [game], LeakyBuilder)
    _corrupt_the_future(loaded_store, [game])
    after = _vectors(loaded_store, [game], LeakyBuilder)
    assert after[game[0]]["pace"] != before[game[0]]["pace"]


def test_market_feature_uses_only_quotes_known_at_decision_time(loaded_store):
    gid, gd, home, away = _sample_games(loaded_store)[0]
    builder = FeatureBuilder(loaded_store)
    early = datetime.combine(date.fromisoformat(gd), time(12, 0), tzinfo=UTC)
    f_early = builder.build(game_date=gd, home=home, away=away, as_of=early, save=False)
    f_tip = FeatureBuilder(loaded_store).build(
        game_date=gd, home=home, away=away, as_of=_decision_time(gd), save=False
    )
    assert f_early.market_prob is None  # the closing line isn't known at noon
    assert f_tip.market_prob is not None
