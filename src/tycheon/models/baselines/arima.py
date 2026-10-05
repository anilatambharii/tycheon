"""ARIMA on log returns, via statsmodels."""

from __future__ import annotations

import warnings

import numpy as np
from statsmodels.tsa.arima.model import ARIMA

from tycheon.models.base import BaseForecaster, PreparedHistory, RawForecast
from tycheon.models.baselines._common import log_returns, prices_from_log_returns

# Returns are tiny (~0.01); the optimiser behaves far better on percent returns.
_SCALE = 100.0


class ARIMAForecaster(BaseForecaster):
    """ARIMA(p, d, q) with a constant, fitted to log returns and simulated forward.

    Sample paths come from ``simulate`` on the fitted model, so they carry the
    model's own serial dependence and innovation variance. Parameter uncertainty is
    *not* propagated: the paths are conditional on the point estimates, which makes
    the intervals optimistic for short histories. Fit warnings (convergence, boundary
    estimates) are recorded in the forecast diagnostics rather than suppressed.
    """

    model_id = "arima"
    model_card = "docs/models/arima.md"
    min_history = 30

    def __init__(
        self,
        *,
        order: tuple[int, int, int] = (1, 0, 1),
        window: int | None = 500,
        seed: int | None = 0,
    ) -> None:
        super().__init__(seed=seed)
        if window is not None and window < self.min_history:
            raise ValueError(f"window must be at least {self.min_history}")
        self.order = order
        self.window = window

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        returns = log_returns(prepared.frame["close"].to_numpy(dtype=np.float64)) * _SCALE
        used = returns if self.window is None else returns[-self.window :]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fitted = ARIMA(used, order=self.order, trend="c").fit()
            simulated = fitted.simulate(
                nsimulations=horizon,
                repetitions=n_samples,
                anchor="end",
                rng=np.random.default_rng(seed),
            )
        paths = np.asarray(simulated, dtype=np.float64).reshape(horizon, n_samples).T / _SCALE
        names = [str(w.message) for w in caught]

        return RawForecast(
            samples=prices_from_log_returns(prepared.last_close, paths),
            model_version="statsmodels-arima-1",
            context_length_used=used.size + 1,
            params={"order": list(self.order), "window": self.window},
            diagnostics={"fit_warnings": names} if names else {},
        )
