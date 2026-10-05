"""The drift method: a random walk that keeps drifting at its historical average rate."""

from __future__ import annotations

import numpy as np

from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast
from tycheon.models.baselines._common import (
    log_returns,
    prices_from_log_returns,
    volatility,
)


class DriftForecaster(BaseForecaster):
    """Log-price random walk with drift equal to the mean historical log return.

    The drift is itself an estimate from ``T`` returns, so a path is drawn with its
    own drift ``mu ~ N(mean, sigma^2 / T)``. That adds the textbook ``h^2 sigma^2 / T``
    term, giving a forecast variance of ``h * sigma^2 * (1 + h / T)`` rather than
    pretending the drift is known.
    """

    model_id = "drift"
    model_card = "docs/models/drift.md"
    min_history = 3

    def __init__(self, *, window: int | None = None, seed: int | None = 0) -> None:
        super().__init__(seed=seed)
        if window is not None and window < 3:
            raise ValueError("window must be at least 3")
        self.window = window

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        returns = log_returns(prepared.frame["close"].to_numpy(dtype=np.float64))
        used = returns if self.window is None else returns[-self.window :]
        mu = float(np.mean(used))
        sigma = volatility(used, None)
        rng = np.random.default_rng(seed)
        path_drift = rng.normal(mu, sigma / np.sqrt(used.size), size=(n_samples, 1))
        shocks = path_drift + rng.normal(0.0, sigma, size=(n_samples, horizon))
        return RawForecast(
            samples=prices_from_log_returns(prepared.last_close, shocks),
            model_version="1",
            context_length_used=used.size + 1,
            params={"window": self.window, "mean_log_return": mu, "sigma_per_step": sigma},
        )
