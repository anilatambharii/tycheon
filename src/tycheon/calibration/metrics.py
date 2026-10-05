"""Proper scoring rules, coverage measures and the finite-sample conformal quantile.

Pure functions on arrays, shared by the calibrators, the diagnostics and the router. All
distributional quantities are in log-return space (see :mod:`tycheon.calibration.scores`).
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from scipy import stats

Floats = NDArray[np.float64]


def conformal_index(n: int, level: float) -> int | None:
    """Order statistic (1-based) of ``n`` scores that is the conformal ``level`` quantile.

    Split conformal needs the *finite-sample* correction: for an upper level the
    ``ceil((n + 1) * level)``-th smallest score, for a lower level the
    ``floor((n + 1) * level)``-th. Each rounds in the conservative direction for its tail, so
    a central interval built from a lower and an upper level has marginal coverage at least
    ``1 - alpha`` under exchangeability. Returns ``None`` when there are too few scores to
    support this level (for example a 1% tail needs at least 99), instead of pretending.
    """
    if not 0.0 < level < 1.0:
        raise ValueError("level must be strictly between 0 and 1")
    k = math.ceil((n + 1) * level) if level >= 0.5 else math.floor((n + 1) * level)
    return k if 1 <= k <= n else None


def conformal_quantile(scores: Floats, level: float) -> float | None:
    """The conformal ``level`` quantile of 1-D ``scores``, or ``None`` if unsupported."""
    k = conformal_index(len(scores), level)
    if k is None:
        return None
    return float(np.partition(scores, k - 1)[k - 1])


def interval_coverage(lo: Floats, hi: Floats, y: Floats) -> float:
    """Share of outcomes inside ``[lo, hi]``."""
    return float(np.mean((y >= lo) & (y <= hi)))


def pinball(level: float, q: Floats, y: Floats) -> Floats:
    """Pinball (quantile) loss of predicted quantile ``q`` at ``level`` against outcome ``y``."""
    diff = y - q
    return np.asarray(np.maximum(level * diff, (level - 1.0) * diff), dtype=np.float64)


def quantile_score(levels: tuple[float, ...], logq: Floats, y: Floats) -> Floats:
    """Mean pinball loss over ``levels`` per origin and step: a proper score on a level grid.

    ``logq`` has shape ``(n, levels, horizon)`` and ``y`` shape ``(n, horizon)``. It is
    comparable between models that share the same levels, including quantile-only ones, which
    is why the router uses it. Lower is better.
    """
    out = np.zeros_like(y)
    for j, level in enumerate(levels):
        out += pinball(level, logq[:, j, :], y)
    return np.asarray(out / len(levels), dtype=np.float64)


def crps_samples(samples: Floats, y: Floats, *, fair: bool = True) -> Floats:
    """CRPS of an ensemble, per origin and step. ``samples`` is ``(n, S, h)``, ``y`` ``(n, h)``.

    ``E|X - y| - 0.5 E|X - X'|`` estimated from the ensemble; ``fair=True`` uses the unbiased
    ``1 / (S (S - 1))`` form so scores do not depend on the ensemble size. Lower is better.
    """
    s = samples.shape[1]
    term1 = np.abs(samples - y[:, None, :]).mean(axis=1)
    ordered = np.sort(samples, axis=1)
    weights = (2.0 * np.arange(1, s + 1) - s - 1.0)[None, :, None]
    pair_sum = 2.0 * (weights * ordered).sum(axis=1)  # sum over i,j of |x_i - x_j|
    denom = s * (s - 1) if fair else s * s
    return np.asarray(term1 - 0.5 * pair_sum / denom, dtype=np.float64)


def crps_gaussian(mu: float | Floats, sigma: float | Floats, y: float | Floats) -> Floats:
    """Closed-form CRPS of ``N(mu, sigma^2)`` at ``y``. Used to check the ensemble estimator."""
    z = (np.asarray(y) - mu) / sigma
    return np.asarray(
        sigma
        * (
            z * (2.0 * stats.norm.cdf(z) - 1.0) + 2.0 * stats.norm.pdf(z) - 1.0 / math.sqrt(math.pi)
        ),
        dtype=np.float64,
    )


def pit_histogram(pit: Floats, bins: int = 10) -> NDArray[np.int64]:
    """Counts of PIT values in ``bins`` equal bins on [0, 1]. Flat means calibrated."""
    counts, _ = np.histogram(np.clip(pit.ravel(), 0.0, 1.0), bins=bins, range=(0.0, 1.0))
    return np.asarray(counts, dtype=np.int64)


def interpolate_shift(u: Floats, levels: tuple[float, ...], delta: Floats) -> Floats:
    """The additive shift at rank ``u``, interpolated over the calibrated level grid.

    Outside the grid the nearest calibrated shift is used (never extrapolated), so the
    extreme tails inherit the adjustment of the most extreme level that had enough scores.
    """
    return np.asarray(np.interp(u, np.asarray(levels), delta), dtype=np.float64)


def shift_samples(log_samples: Floats, levels: tuple[float, ...], delta: Floats) -> Floats:
    """Apply a calibrated quantile shift to sample paths, preserving each step's ranking.

    ``log_samples`` is ``(S, h)``; ``delta`` is ``(levels, h)``. A sample at rank ``u`` within
    its step is moved by the shift for level ``u``. Because the shift depends only on rank,
    cross-step dependence (which paths are the bad ones) is kept, while each step's marginal
    distribution moves to the calibrated one. A monotone repair guarantees ordering within a
    step is not inverted by the shift.
    """
    s, h = log_samples.shape
    out = np.empty_like(log_samples)
    for step in range(h):
        column = log_samples[:, step]
        order = np.argsort(column, kind="stable")
        ranks = (np.arange(s) + 0.5) / s
        moved = column[order] + interpolate_shift(ranks, levels, delta[:, step])
        out[order, step] = np.maximum.accumulate(moved)
    return out
