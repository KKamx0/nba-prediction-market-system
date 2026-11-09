import numpy as np
import pytest

from edgebot.model.ratings import fit_ratings, team_strength
from tests.synthetic import make_league


@pytest.fixture(scope="module")
def league():
    return make_league(n_games=2460, seed=1)  # ~two seasons of games


def _corr(est: dict, true: dict) -> float:
    keys = sorted(true)
    return float(np.corrcoef([est[k] for k in keys], [true[k] for k in keys])[0, 1])


def test_recovers_true_ratings_and_home_court(league):
    r = fit_ratings(league.team_games, league.player_games)
    net_true = {p: league.true_off[p] + league.true_def[p] for p in league.true_off}
    net_est = {p: r.net(p) for p in league.true_off}
    # game-level plus-minus is noisy per player; see module docstring
    assert _corr(net_est, net_true) > 0.55
    assert r.hca == pytest.approx(league.hca, abs=1.5)
    assert 100 < r.league_eff < 125


def test_team_strength_ranks_real_team_quality(league):
    r = fit_ratings(league.team_games, league.player_games)
    true_team, est_team = {}, {}
    for team, roster in league.rosters.items():
        mins = {p: 24.0 for p in roster}
        true_team[team] = sum(
            5 * 24 / 240 * (league.true_off[p] + league.true_def[p]) for p in roster
        )
        est_team[team] = team_strength(r, mins)
    assert _corr(est_team, true_team) > 0.85


def test_no_data_returns_the_prior():
    prior = {7: (2.0, -1.0)}
    r = fit_ratings([], [], prior=prior)
    assert (r.o(7), r.d(7)) == (2.0, -1.0)
    unseen = r.config.rookie_prior
    assert (r.o(999), r.d(999)) == unseen


def test_prior_phases_out_as_minutes_accumulate(league):
    # Give every player a wrong prior of +5 net. With few games the fit stays
    # near it; with a full sample it moves toward the truth.
    wrong = {p: (2.5, 2.5) for p in league.true_off}
    ids = sorted({r["game_id"] for r in league.team_games})

    def mean_abs_error(n_games):
        keep = set(ids[:n_games])
        tg = [r for r in league.team_games if r["game_id"] in keep]
        pg = [r for r in league.player_games if r["game_id"] in keep]
        r = fit_ratings(tg, pg, prior=wrong)
        return np.mean(
            [abs(r.net(p) - (league.true_off[p] + league.true_def[p])) for p in league.true_off]
        )

    few, many = mean_abs_error(60), mean_abs_error(2460)
    assert many < few


def test_carryover_shrinks_prior_toward_zero(league):
    r = fit_ratings(league.team_games, league.player_games)
    p = next(iter(league.true_off))
    o, d = r.as_prior()[p]
    assert o == pytest.approx(r.config.carryover * r.o(p))
    assert d == pytest.approx(r.config.carryover * r.d(p))


def test_team_strength_handles_empty_lineup(league):
    r = fit_ratings(league.team_games, league.player_games)
    assert team_strength(r, {}) == 0.0
