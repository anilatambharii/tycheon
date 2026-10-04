"""The random walk: "tomorrow looks like today, give or take the usual noise".

This is the baseline every evaluation in Tycheon is read against (AGENTS.md), and
the one that is hardest to beat honestly. It forecasts the log price as a driftless
Gaussian random walk whose per-step volatility is estimated from the recent past, so
the *median* forecast is exactly the last close and uncertainty widens with the
square root of the horizon.
"""

from __future__ import annotations

import numpy as np

from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast
from tycheon.models.baselines._common import (
    log_returns,
    prices_from_log_returns,
    volatility,
)


class RandomWalkForecaster(BaseForecaster):
    """Driftless log-price random walk with volatility from the last ``window`` returns."""

    model_id = "random-walk"
    model_card = "docs/models/random-walk.md"
    min_history = 3

    def __init__(self, *, window: int | None = 250, seed: int | None = 0) -> None:
        super().__init__(seed=seed)
        if window is not None and window < 2:
            raise ValueError("window must be at least 2")
        self.window = window

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        returns = log_returns(prepared.frame["close"].to_numpy(dtype=np.float64))
        sigma = volatility(returns, self.window)
        used = returns.size if self.window is None else min(self.window, returns.size)
        rng = np.random.default_rng(seed)
        shocks = rng.normal(0.0, sigma, size=(n_samples, horizon))
        return RawForecast(
            samples=prices_from_log_returns(prepared.last_close, shocks),
            model_version="1",
            context_length_used=used + 1,
            params={"window": self.window, "sigma_per_step": sigma, "drift": 0.0},
        )
