import pytest

from edgebot.devig import (
    american_to_decimal,
    consensus_home_prob,
    devig_multiplicative,
    devig_power,
    fair_home_prob,
    overround,
)


@pytest.mark.parametrize(
    "american,decimal_odds",
    [(100, 2.0), (-100, 2.0), (150, 2.5), (-200, 1.5), (-110, 1 + 100 / 110)],
)
def test_american_to_decimal(american, decimal_odds):
    assert american_to_decimal(american) == pytest.approx(decimal_odds)


@pytest.mark.parametrize("bad", [0, 50, -99, float("nan"), float("inf")])
def test_invalid_american_odds_rejected(bad):
    with pytest.raises(ValueError):
        american_to_decimal(bad)


def test_standard_minus110_line_is_fifty_fifty():
    d = american_to_decimal(-110)
    assert overround(d, d) == pytest.approx(0.0476, abs=1e-4)
    assert devig_power(d, d) == pytest.approx((0.5, 0.5))
    assert devig_multiplicative(d, d) == pytest.approx((0.5, 0.5))


@pytest.mark.parametrize("method", [devig_power, devig_multiplicative])
@pytest.mark.parametrize("home,away", [(1.25, 4.2), (2.1, 1.8), (1.05, 12.0), (3.3, 1.38)])
def test_fair_probs_sum_to_one_and_keep_order(method, home, away):
    ph, pa = method(home, away)
    assert ph + pa == pytest.approx(1.0)
    assert 0 < ph < 1 and 0 < pa < 1
    assert (ph > pa) == (home < away)


def test_power_method_shades_the_longshot_more():
    fav, dog = american_to_decimal(-400), american_to_decimal(300)
    p_mult = devig_multiplicative(fav, dog)[0]
    p_pow = devig_power(fav, dog)[0]
    assert p_pow > p_mult  # favorite gets a little more, longshot a little less


def test_no_vig_line_is_unchanged():
    assert devig_power(2.0, 2.0) == pytest.approx((0.5, 0.5))


def test_consensus_uses_median_and_drops_broken_quotes():
    good = [(1.5, 2.7), (1.52, 2.65), (1.48, 2.75)]
    broken = (1.1, 1.1)  # 82% overround: stale or mis-keyed
    p = consensus_home_prob(good + [broken])
    expected = sorted(fair_home_prob(h, a) for h, a in good)[1]
    assert p == pytest.approx(expected)
    assert consensus_home_prob([broken]) is None
    assert consensus_home_prob([]) is None
