"""Regime detection from price history, strictly causal.

A regime label at bar ``t`` may use only bars up to ``t``. Two detectors:

* :class:`VolatilityRegimeDetector`: clusters *rolling realised volatility*. The cut points
  between regimes are re-estimated at every bar from a trailing window, so a calm spell is
  "calm" relative to what was recent, with no look-ahead. Fast and deterministic.
* :class:`MarkovSwitchingRegimeDetector`: a two-state Markov-switching model of returns
  (``statsmodels``), reading *filtered* (not smoothed) probabilities, which condition only on
  data up to each bar. Its parameters are fitted once on the trailing window ending at the last
  bar, so labels for earlier bars in that window use parameters estimated with slightly later
  data (still nothing after the forecast's ``as_of``). Use it for the current regime and
  recent history; use the volatility detector when each historical label must be fully causal.

Both return integer labels ordered by volatility (0 is the calmest) and ``-1`` where there is
not yet enough history.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Protocol

import numpy as np
from numpy.typing import NDArray
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    import pandas as pd

Ints = NDArray[np.int64]
Floats = NDArray[np.float64]


class RegimeDetector(Protocol):
    """Anything that labels each bar with a regime using only data up to that bar."""

    n_regimes: int

    def labels(self, bars: pd.DataFrame) -> Ints:
        """One label per bar of ``bars`` (``-1`` where undefined)."""
        ...


def _log_returns(bars: pd.DataFrame) -> Floats:
    if "close" not in bars.columns:
        raise DataValidationError("regime detection needs a close column")
    close = bars["close"].to_numpy(dtype=np.float64)
    if (close <= 0).any():
        raise DataValidationError("regime detection needs positive closes")
    return np.concatenate(([np.nan], np.diff(np.log(close))))


def rolling_volatility(returns: Floats, window: int) -> Floats:
    """Trailing standard deviation of ``returns`` over ``window`` bars (NaN until available)."""
    out = np.full_like(returns, np.nan)
    for t in range(window, len(returns)):
        out[t] = np.std(returns[t - window + 1 : t + 1], ddof=1)
    return out


def _kmeans_1d(x: Floats, k: int) -> Floats:
    """Centres of a deterministic 1-D k-means (quantile initialisation), ascending."""
    centres = np.quantile(x, (np.arange(k) + 0.5) / k)
    for _ in range(25):
        assign = np.argmin(np.abs(x[:, None] - centres[None, :]), axis=1)
        updated = np.array(
            [x[assign == c].mean() if (assign == c).any() else centres[c] for c in range(k)]
        )
        if np.allclose(updated, centres):
            break
        centres = updated
    return np.sort(centres)


class VolatilityRegimeDetector:
    """Cluster rolling volatility into ``n_regimes`` regimes with cut points re-fitted causally.

    Args:
        window: Bars in each realised-volatility estimate.
        lookback: Trailing bars of volatility used to place the cut points at each bar.
        method: ``"quantile"`` (equal-occupancy cut points) or ``"kmeans"`` (cut points midway
            between 1-D cluster centres of log-volatility, which respects uneven occupancy).
    """

    def __init__(
        self,
        *,
        n_regimes: int = 2,
        window: int = 20,
        lookback: int = 500,
        min_history: int = 120,
        method: str = "kmeans",
    ) -> None:
        if n_regimes < 2:
            raise ValueError("n_regimes must be at least 2")
        if method not in ("quantile", "kmeans"):
            raise ValueError("method must be 'quantile' or 'kmeans'")
        if min_history <= window:
            raise ValueError("min_history must exceed window")
        self.n_regimes = n_regimes
        self.window = window
        self.lookback = lookback
        self.min_history = min_history
        self.method = method

    def _cuts(self, trailing: Floats) -> Floats:
        if self.method == "quantile":
            return np.quantile(trailing, np.arange(1, self.n_regimes) / self.n_regimes)
        centres = _kmeans_1d(np.log(trailing), self.n_regimes)
        return np.exp((centres[:-1] + centres[1:]) / 2.0)

    def labels(self, bars: pd.DataFrame) -> Ints:
        vol = rolling_volatility(_log_returns(bars), self.window)
        out = np.full(len(bars), -1, dtype=np.int64)
        for t in range(self.min_history, len(bars)):
            trailing = vol[max(self.window, t - self.lookback + 1) : t + 1]
            trailing = trailing[np.isfinite(trailing) & (trailing > 0)]
            if len(trailing) < self.n_regimes * 5:
                continue
            out[t] = int(np.searchsorted(self._cuts(trailing), vol[t], side="right"))
        return out


class MarkovSwitchingRegimeDetector:
    """Two-state Markov-switching returns model, labelled from filtered probabilities.

    Args:
        lookback: Trailing bars the model is fitted on (earlier bars get ``-1``).
    """

    def __init__(self, *, lookback: int = 750, min_history: int = 200) -> None:
        self.n_regimes = 2
        self.lookback = lookback
        self.min_history = min_history

    def labels(self, bars: pd.DataFrame) -> Ints:
        returns = _log_returns(bars)
        out = np.full(len(bars), -1, dtype=np.int64)
        usable = returns[1:]
        if len(usable) < self.min_history:
            return out
        window = usable[-self.lookback :] * 100.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted = MarkovRegression(window, k_regimes=2, trend="c", switching_variance=True).fit(
                disp=False
            )
        named = dict(zip(fitted.model.param_names, np.asarray(fitted.params), strict=True))
        variances = np.array([float(named[f"sigma2[{k}]"]) for k in range(2)])
        calm = int(np.argmin(variances))
        filtered = np.asarray(fitted.filtered_marginal_probabilities)  # (T, 2), causal
        label = (filtered[:, 1 - calm] > filtered[:, calm]).astype(np.int64)
        out[len(bars) - len(window) :] = label
        return out
