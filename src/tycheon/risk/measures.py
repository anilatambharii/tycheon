"""Risk measures from sample paths: VaR, Expected Shortfall, drawdown and volatility.

Everything here is computed from **joint sample paths** of a value (a price, or a portfolio's
market value). That is why quantile-only forecasts (TimesFM, Chronos) are refused: an
Expected Shortfall needs the *shape* of the tail, and a drawdown needs how steps move
together, and neither can be recovered from marginal quantiles without inventing an assumption
nobody measured.

Conventions
-----------
* A loss is positive. ``VaR(level)`` is the loss exceeded with probability ``1 - level``;
  ``ES(level)`` is the average loss in that worst ``1 - level`` tail.
* Returns are *simple* returns over the horizon step, ``value[t] / start - 1``.
* Each measure carries a standard error (bootstrap), the number of paths in the tail, and
  whether it can be trusted: a 99% ES from 50 paths averages half a path and says so.
* ``calibrated`` is true only when the input was calibrated *and* the measure's tail lies
  inside the range of levels the calibration covered, so a 99% VaR is never labelled
  calibrated just because a 90% interval was.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from tycheon.errors import ModelError

if TYPE_CHECKING:
    import pandas as pd

    from tycheon.models.base import CalibrationInfo, CalibrationStatus, ForecastDistribution

Floats = NDArray[np.float64]

DEFAULT_LEVELS: tuple[float, ...] = (0.9, 0.95, 0.975, 0.99)
DEFAULT_DRAWDOWNS: tuple[float, ...] = (0.05, 0.10, 0.20)
MIN_TAIL_PATHS = 10


@dataclass(frozen=True)
class RiskInput:
    """Joint sample paths of a value plus the provenance every risk output must carry."""

    name: str
    paths: Floats  #: ``(n_paths, horizon)``
    start_value: float
    index: pd.DatetimeIndex
    as_of: pd.Timestamp
    calibration_status: CalibrationStatus
    calibration: CalibrationInfo | None
    model_mix: dict[str, float]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.paths.ndim != 2 or self.paths.shape[1] != len(self.index):
            raise ValueError("paths must be (n_paths, horizon) matching index")
        if self.start_value <= 0:
            raise ValueError("start_value must be positive")

    @classmethod
    def from_forecast(cls, dist: ForecastDistribution, *, name: str | None = None) -> RiskInput:
        """Risk input from a forecast. Refuses forecasts without joint sample paths."""
        paths = dist.require_paths("risk measures (VaR, Expected Shortfall, drawdown)")
        return cls(
            name=name or dist.metadata.model_id,
            paths=np.asarray(paths, dtype=np.float64),
            start_value=dist.last_close,
            index=dist.index,
            as_of=dist.as_of,
            calibration_status=dist.calibration_status,
            calibration=dist.calibration,
            model_mix=dict(dist.model_mix),
        )

    @property
    def n_paths(self) -> int:
        return int(self.paths.shape[0])

    @property
    def horizon(self) -> int:
        return int(self.paths.shape[1])

    def returns(self, step: int = -1) -> Floats:
        """Simple return from the start to ``step`` (default: the end of the horizon)."""
        return np.asarray(self.paths[:, step] / self.start_value - 1.0, dtype=np.float64)

    def tail_is_calibrated(self, tail: float) -> bool:
        """Whether lower-tail probability ``tail`` lies inside the calibrated level range."""
        info = self.calibration
        if self.calibration_status != "calibrated" or info is None or not info.calibrated_levels:
            return False
        return min(info.calibrated_levels) - 1e-12 <= tail <= max(info.calibrated_levels) + 1e-12


@dataclass(frozen=True)
class RiskMeasure:
    """One VaR or ES estimate at one confidence level."""

    kind: str
    level: float
    step: int
    value: float
    se: float
    n_tail: int
    reliable: bool
    calibrated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "level": self.level,
            "step": self.step,
            "loss_fraction": self.value,
            "standard_error": self.se,
            "n_tail_paths": self.n_tail,
            "reliable": self.reliable,
            "calibrated": self.calibrated,
        }


def _var_es(returns: Floats, level: float) -> tuple[float, float, int]:
    n = len(returns)
    k = max(1, math.ceil(n * (1.0 - level)))
    ordered = np.sort(returns)
    var = -float(np.quantile(returns, 1.0 - level))
    es = -float(ordered[:k].mean())
    return var, es, k


def value_at_risk_and_es(
    inp: RiskInput,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
    *,
    step: int = -1,
    n_boot: int = 200,
    seed: int = 0,
) -> list[RiskMeasure]:
    """VaR and Expected Shortfall at each confidence level, from the paths."""
    if any(not 0.5 < level < 1.0 for level in levels):
        raise ValueError("confidence levels must lie in (0.5, 1)")
    returns = inp.returns(step)
    rng = np.random.default_rng(seed)
    boots = rng.integers(0, len(returns), size=(n_boot, len(returns)))
    resampled = returns[boots]
    out: list[RiskMeasure] = []
    for level in levels:
        var, es, k = _var_es(returns, level)
        boot_var = -np.quantile(resampled, 1.0 - level, axis=1)
        boot_es = np.array([-np.sort(r)[:k].mean() for r in resampled])
        calibrated = inp.tail_is_calibrated(1.0 - level)
        reliable = k >= MIN_TAIL_PATHS
        out.append(
            RiskMeasure("VaR", level, step, var, float(boot_var.std()), k, reliable, calibrated)
        )
        out.append(
            RiskMeasure("ES", level, step, es, float(boot_es.std()), k, reliable, calibrated)
        )
    return out


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (better than normal near 0 and 1)."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class ProbabilityEstimate:
    """A probability estimated from paths, with its Wilson interval."""

    threshold: float
    probability: float
    low: float
    high: float
    n_paths: int

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "probability": self.probability,
            "ci_low": self.low,
            "ci_high": self.high,
            "n_paths": self.n_paths,
        }


def max_drawdown(inp: RiskInput) -> Floats:
    """Largest peak-to-trough fall per path over the horizon, as a fraction of the peak.

    The running peak includes the starting value, so a path that only ever falls has a
    drawdown equal to its total fall.
    """
    with_start = np.concatenate([np.full((inp.n_paths, 1), inp.start_value), inp.paths], axis=1)
    peak = np.maximum.accumulate(with_start, axis=1)
    return np.asarray((1.0 - with_start / peak).max(axis=1), dtype=np.float64)


def drawdown_probabilities(
    inp: RiskInput, thresholds: tuple[float, ...] = DEFAULT_DRAWDOWNS
) -> list[ProbabilityEstimate]:
    """P(max drawdown over the horizon exceeds ``x``) for each ``x``."""
    dd = max_drawdown(inp)
    out = []
    for x in thresholds:
        hits = int((dd >= x).sum())
        low, high = wilson_interval(hits, len(dd))
        out.append(ProbabilityEstimate(x, hits / len(dd), low, high, len(dd)))
    return out


def loss_probabilities(
    inp: RiskInput, thresholds: tuple[float, ...] = DEFAULT_DRAWDOWNS, *, step: int = -1
) -> list[ProbabilityEstimate]:
    """P(loss at ``step`` exceeds ``x``): the end-of-horizon counterpart of drawdown."""
    r = inp.returns(step)
    out = []
    for x in thresholds:
        hits = int((r <= -x).sum())
        low, high = wilson_interval(hits, len(r))
        out.append(ProbabilityEstimate(x, hits / len(r), low, high, len(r)))
    return out


@dataclass(frozen=True)
class VolatilityForecast:
    """Volatility implied by the paths, per bar and annualised."""

    horizon_vol_per_bar: float
    annualized: float
    path_realized_vol_quantiles: dict[float, float]
    annualization: float
    model_sigma_per_bar: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "horizon_vol_per_bar": self.horizon_vol_per_bar,
            "annualized": self.annualized,
            "annualization_bars": self.annualization,
            "path_realized_vol_quantiles": {
                f"{q:g}": v for q, v in self.path_realized_vol_quantiles.items()
            },
            "model_sigma_per_bar": self.model_sigma_per_bar,
        }


def volatility_forecast(
    inp: RiskInput, *, annualization: float = 252.0, model_sigma: Floats | None = None
) -> VolatilityForecast:
    """Volatility from the dispersion of horizon returns and from each path's own realised vol.

    ``horizon_vol_per_bar`` is the cross-path standard deviation of the log return over the
    horizon, divided by ``sqrt(horizon)``; the per-path figure is the standard deviation of
    each path's bar-by-bar log returns (needs at least two bars).
    """
    log_total = np.log(inp.paths[:, -1] / inp.start_value)
    per_bar = float(log_total.std(ddof=1) / math.sqrt(inp.horizon))
    quantiles: dict[float, float] = {}
    if inp.horizon >= 2:
        full = np.concatenate([np.full((inp.n_paths, 1), inp.start_value), inp.paths], axis=1)
        step = np.diff(np.log(full), axis=1)
        realized = step.std(axis=1, ddof=1)
        quantiles = {q: float(np.quantile(realized, q)) for q in (0.05, 0.5, 0.95)}
    sigma = None if model_sigma is None else float(np.sqrt(np.mean(np.asarray(model_sigma) ** 2)))
    return VolatilityForecast(
        per_bar, per_bar * math.sqrt(annualization), quantiles, annualization, sigma
    )


def require_paths_for(dist: ForecastDistribution, what: str) -> None:
    """Raise a clear error if ``dist`` has no joint paths (kept for callers that pre-check)."""
    if not dist.has_paths:
        raise ModelError(f"{what} needs joint sample paths; {dist.metadata.model_id} has none")
