import numpy as np
import pytest

from edgebot.model.metrics import accuracy, brier, calibration_bins, ece, log_loss


def test_perfect_and_coinflip_scores():
    y = np.array([1, 0, 1, 0])
    assert log_loss(y, [0.5] * 4) == pytest.approx(np.log(2))
    assert brier(y, [0.5] * 4) == pytest.approx(0.25)
    assert log_loss(y, y) < 1e-5
    assert brier(y, y) == pytest.approx(0, abs=1e-9)


def test_extreme_wrong_predictions_are_clipped_not_infinite():
    assert np.isfinite(log_loss([1], [0.0]))


def test_ece_zero_for_calibrated_and_positive_for_overconfident():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 50_000)
    y = (rng.uniform(size=p.size) < p).astype(float)
    assert ece(y, p) < 0.01
    overconfident = np.clip(0.5 + 1.6 * (p - 0.5), 0.01, 0.99)
    assert ece(y, overconfident) > 0.05


def test_calibration_bins_skip_empty_and_count_everything():
    bins = calibration_bins([0, 1, 1], [0.1, 0.9, 0.95])
    assert sum(c for *_, c in bins) == 3
    assert len(bins) == 2


def test_accuracy_and_shape_checks():
    assert accuracy([1, 0], [0.7, 0.2]) == 1.0
    with pytest.raises(ValueError):
        log_loss([1, 0], [0.5])
    with pytest.raises(ValueError):
        brier([], [])
