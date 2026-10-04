"""Helpers shared by the baselines. All of them model log prices, so paths stay positive."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Floats = NDArray[np.float64]


def log_returns(close: Floats) -> Floats:
    """One-step log returns of a positive price series."""
    return np.diff(np.log(close))


def volatility(returns: Floats, window: int | None) -> float:
    """Sample standard deviation of the last ``window`` returns (all of them if ``None``)."""
    used = returns if window is None else returns[-window:]
    if used.size < 2:
        raise ValueError("need at least two returns to estimate volatility")
    sigma = float(np.std(used, ddof=1))
    # A flat series has zero variance; an exactly-zero spread would claim certainty
    # that no market has ever earned, so floor it at something negligible but nonzero.
    return max(sigma, 1e-12)


def prices_from_log_returns(last_close: float, log_returns_paths: Floats) -> Floats:
    """Turn ``(n_paths, horizon)`` log-return paths into price paths from ``last_close``."""
    return np.asarray(last_close * np.exp(np.cumsum(log_returns_paths, axis=1)), dtype=np.float64)
