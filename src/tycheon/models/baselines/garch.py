"""GARCH(1,1) volatility, via the ``arch`` package, turned into price paths."""

from __future__ import annotations

import warnings

import numpy as np
from arch import arch_model

from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast
from tycheon.models.baselines._common import log_returns, prices_from_log_returns

# Same reason as ARIMA: GARCH optimisers want percent returns.
_SCALE = 100.0


class GARCHForecaster(BaseForecaster):
    """Constant-mean GARCH(1,1) with Gaussian innovations.

    This is the baseline for the thing foundation models are usually not asked
    about: *how volatile will it be*. Price paths are simulated from the fitted
    conditional-variance recursion, so volatility clustering shows up as fatter
    horizon-end tails than a constant-volatility random walk. The per-step
    conditional standard deviation is returned in ``extras["sigma"]`` as a
    log-return fraction (not percent), shape ``(horizon,)``.

    Non-stationary fits (``alpha + beta >= 1``) are flagged in the diagnostics.
    """

    model_id = "garch"
    model_card = "docs/models/garch.md"
    min_history = 100

    def __init__(self, *, window: int | None = 1000, seed: int | None = 0) -> None:
        super().__init__(seed=seed)
        if window is not None and window < self.min_history:
            raise ValueError(f"window must be at least {self.min_history}")
        self.window = window

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        returns = log_returns(prepared.frame["close"].to_numpy(dtype=np.float64)) * _SCALE
        used = returns if self.window is None else returns[-self.window :]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = arch_model(used, mean="Constant", vol="GARCH", p=1, q=1, dist="normal").fit(
                disp="off", show_warning=False
            )
            forecast = result.forecast(
                horizon=horizon,
                method="simulation",
                simulations=n_samples,
                # arch only reads random_state for the bootstrap method; simulation draws
                # its standard-normal innovations from rng, so that is what must be seeded.
                rng=np.random.default_rng(seed).standard_normal,
                reindex=False,
            )
        simulated = np.asarray(forecast.simulations.values, dtype=np.float64)[-1]
        paths = simulated / _SCALE
        sigma = np.sqrt(np.asarray(forecast.variance.values, dtype=np.float64)[-1]) / _SCALE

        params = result.params
        alpha, beta = float(params["alpha[1]"]), float(params["beta[1]"])
        diagnostics: dict[str, object] = {"alpha": alpha, "beta": beta, "persistence": alpha + beta}
        if alpha + beta >= 1.0:
            diagnostics["non_stationary"] = True
        names = [str(w.message) for w in caught]
        if names:
            diagnostics["fit_warnings"] = names

        return RawForecast(
            samples=prices_from_log_returns(prepared.last_close, paths),
            extras={"sigma": sigma},
            model_version="arch-garch11-1",
            context_length_used=used.size + 1,
            params={"p": 1, "q": 1, "dist": "normal", "window": self.window},
            diagnostics=diagnostics,
        )
