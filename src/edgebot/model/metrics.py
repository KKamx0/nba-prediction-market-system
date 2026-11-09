"""Probabilistic scoring. Models are compared on these, never on accuracy."""

from __future__ import annotations

import numpy as np

EPS = 1e-6


def _arrays(y, p) -> tuple[np.ndarray, np.ndarray]:  # noqa: ANN001
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), EPS, 1 - EPS)
    if y.shape != p.shape or y.size == 0:
        raise ValueError("y and p must be non-empty and the same shape")
    return y, p


def log_loss(y, p) -> float:  # noqa: ANN001
    y, p = _arrays(y, p)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p) -> float:  # noqa: ANN001
    y, p = _arrays(y, p)
    return float(np.mean((p - y) ** 2))


def calibration_bins(y, p, n_bins: int = 10) -> list[tuple[float, float, int]]:  # noqa: ANN001
    """(mean predicted, observed rate, count) per equal-width bin, skipping empty bins."""
    y, p = _arrays(y, p)
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)
    out = []
    for b in range(n_bins):
        mask = idx == b
        if mask.any():
            out.append((float(p[mask].mean()), float(y[mask].mean()), int(mask.sum())))
    return out


def ece(y, p, n_bins: int = 10) -> float:  # noqa: ANN001
    """Expected calibration error: count-weighted |predicted - observed| over bins."""
    bins = calibration_bins(y, p, n_bins)
    n = sum(c for _, _, c in bins)
    return float(sum(c * abs(pm - obs) for pm, obs, c in bins) / n)


def accuracy(y, p) -> float:  # noqa: ANN001
    """Reported for context only; never used to select a model."""
    y, p = _arrays(y, p)
    return float(np.mean((p > 0.5) == (y > 0.5)))
