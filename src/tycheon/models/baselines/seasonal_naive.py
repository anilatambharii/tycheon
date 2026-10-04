"""Seasonal naive: "the next ``m`` bars look like the last ``m`` bars".

Applied to log prices this is a deliberately weak baseline. It earns its place by
being the standard comparator for seasonal series and by failing loudly on a series
that has no seasonality, which is information the leaderboard should show.
"""

from __future__ import annotations

import numpy as np

from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast


class SeasonalNaiveForecaster(BaseForecaster):
    """Seasonal random walk on log prices: ``y[t] = y[t - m] + e[t]``.

    The forecast simulates exactly that model, drawing ``e`` from a Gaussian whose
    standard deviation is estimated from the history's own seasonal differences
    ``y[t] - y[t - m]``. Because paths are simulated recursively, steps ``m`` apart
    share their innovations and the paths are properly joint, not independent draws.
    """

    model_id = "seasonal-naive"
    model_card = "docs/models/seasonal-naive.md"

    def __init__(self, *, season_length: int = 5, seed: int | None = 0) -> None:
        super().__init__(seed=seed)
        if season_length < 1:
            raise ValueError("season_length must be at least 1")
        self.season_length = season_length
        self.min_history = season_length + 3

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        m = self.season_length
        log_price = np.log(prepared.frame["close"].to_numpy(dtype=np.float64))
        seasonal_diff = log_price[m:] - log_price[:-m]
        sigma = max(float(np.std(seasonal_diff, ddof=1)), 1e-12)

        rng = np.random.default_rng(seed)
        shocks = rng.normal(0.0, sigma, size=(n_samples, horizon))
        # Rolling buffer of the last m log prices per path, then step forward.
        buffer = np.tile(log_price[-m:], (n_samples, 1))
        out = np.empty((n_samples, horizon))
        for step in range(horizon):
            slot = step % m
            buffer[:, slot] = buffer[:, slot] + shocks[:, step]
            out[:, step] = buffer[:, slot]

        return RawForecast(
            samples=np.exp(out),
            model_version="1",
            context_length_used=len(log_price),
            params={"season_length": m, "sigma_seasonal_difference": sigma},
        )
