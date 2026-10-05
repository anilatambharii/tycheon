"""Portfolio aggregation from per-asset sample paths and a documented dependence assumption.

Each asset is forecast on its own, so its sample paths say nothing about how it moves with the
others. To get a portfolio distribution the paths must be *coupled*. The approach here is
explicit and simple:

1. Estimate a correlation matrix of daily log returns from history known at ``as_of``, shrunk
   toward the identity (so a short, noisy sample does not claim certainty about dependence) and
   projected to a valid correlation matrix.
2. Draw correlated Gaussian scores with that matrix (a **Gaussian copula**).
3. Re-order the sample paths (Iman-Conover) so ranks follow those scores. Two schemes:

   * ``"terminal"`` (default) couples the **end-of-horizon returns**. Each path stays intact,
     so its own temporal shape (volatility clustering, mean reversion) and each asset's
     calibrated marginal distribution are untouched; only *which* path is paired with which
     changes. The catch: the target correlation ``rho`` holds at the end of the horizon, and
     at step ``k`` of ``H`` the cross-asset correlation of cumulative returns is only about
     ``rho * k / H``. Terminal VaR and ES are right; intermediate steps and drawdowns see
     *less* co-movement than the target, so they are understated.
   * ``"stepwise"`` couples the **per-step returns** at every step, so correlation is
     ``rho`` throughout and drawdowns see the full co-movement. The catch: it re-pairs
     increments, which breaks each asset's within-path serial dependence. Use it for assets
     whose steps are close to independent, or when drawdowns matter more than path shape.
4. Value a buy-and-hold portfolio along the coupled paths.

What this assumes, and what to read into it: a Gaussian copula has **no tail dependence**, but
real assets tend to fall together in a crisis, so the portfolio's lower tail is likely
*understated*. ``stress_correlation`` raises correlations toward one to see how much. And because
the dependence is assumed rather than calibrated, a multi-asset portfolio is labelled
``uncalibrated`` even when every marginal is calibrated.

If you have genuinely joint paths (one simulator for all assets), pass them straight to
:func:`aggregate_joint_paths`, which does no coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from tycheon.data.asof import as_utc, assert_available
from tycheon.errors import DataValidationError, ModelError
from tycheon.risk.measures import RiskInput

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from tycheon.models.base import ForecastDistribution

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class Position:
    """A buy-and-hold position, by market value in the portfolio currency."""

    symbol: str
    value: float


@dataclass(frozen=True)
class Portfolio:
    """A set of buy-and-hold positions. No rebalancing, no leverage, no costs."""

    positions: tuple[Position, ...]
    name: str = "portfolio"
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.positions:
            raise DataValidationError("a portfolio needs at least one position")
        symbols = [p.symbol for p in self.positions]
        if len(set(symbols)) != len(symbols):
            raise DataValidationError("duplicate symbols in portfolio")
        if any(p.value <= 0 for p in self.positions):
            raise DataValidationError("position values must be positive (long-only)")

    @classmethod
    def from_values(cls, values: Mapping[str, float], *, name: str = "portfolio") -> Portfolio:
        return cls(tuple(Position(s, float(v)) for s, v in values.items()), name=name)

    @property
    def symbols(self) -> list[str]:
        return [p.symbol for p in self.positions]

    @property
    def total_value(self) -> float:
        return float(sum(p.value for p in self.positions))

    @property
    def weights(self) -> dict[str, float]:
        total = self.total_value
        return {p.symbol: p.value / total for p in self.positions}


def estimate_correlation(
    bars_by_symbol: Mapping[str, pd.DataFrame],
    *,
    as_of: datetime,
    lookback: int = 500,
    shrinkage: float = 0.2,
) -> pd.DataFrame:
    """Shrunk correlation of daily log returns from bars known at ``as_of``.

    Raises ``LookaheadError`` if any frame holds data published after ``as_of``.
    """
    as_of_ts = as_utc(as_of)
    if not 0.0 <= shrinkage <= 1.0:
        raise ValueError("shrinkage must be in [0, 1]")
    closes = {}
    for symbol, bars in bars_by_symbol.items():
        assert_available(bars, as_of_ts)
        closes[symbol] = bars["close"]
    frame = pd.DataFrame(closes).dropna().iloc[-(lookback + 1) :]
    if len(frame) < 30:
        raise DataValidationError(
            f"only {len(frame)} common bars to estimate correlation (need 30)"
        )
    corr = np.corrcoef(np.diff(np.log(frame.to_numpy()), axis=0), rowvar=False)
    corr = np.atleast_2d(corr)
    shrunk = (1.0 - shrinkage) * corr + shrinkage * np.eye(len(corr))
    return pd.DataFrame(_nearest_correlation(shrunk), index=frame.columns, columns=frame.columns)


def _nearest_correlation(matrix: Floats) -> Floats:
    """Clip negative eigenvalues and rescale to unit diagonal."""
    sym = (matrix + matrix.T) / 2.0
    vals, vecs = np.linalg.eigh(sym)
    fixed = (vecs * np.clip(vals, 1e-8, None)) @ vecs.T
    d = np.sqrt(np.diag(fixed))
    return np.asarray(fixed / np.outer(d, d), dtype=np.float64)


def stress_correlation(corr: pd.DataFrame, level: float = 0.8) -> pd.DataFrame:
    """Raise every off-diagonal correlation to at least ``level``: a crisis-style coupling."""
    if not 0.0 <= level <= 1.0:
        raise ValueError("level must be in [0, 1]")
    out = corr.to_numpy(copy=True)
    off = ~np.eye(len(out), dtype=bool)
    out[off] = np.maximum(out[off], level)
    return pd.DataFrame(_nearest_correlation(out), index=corr.index, columns=corr.columns)


def _couple_stepwise(
    paths: Mapping[str, Floats],
    start: Mapping[str, float],
    corr: Floats,
    symbols: list[str],
    seed: int,
) -> dict[str, Floats]:
    """Couple the per-step log returns of every step with the same Gaussian copula."""
    count, horizon = next(iter(paths.values())).shape
    rng = np.random.default_rng(seed)
    scores = rng.multivariate_normal(np.zeros(len(symbols)), corr, size=(count, horizon))
    out: dict[str, Floats] = {}
    for a, symbol in enumerate(symbols):
        first = np.full((count, 1), start[symbol])
        increments = np.diff(np.log(np.concatenate([first, paths[symbol]], axis=1)), axis=1)
        coupled = np.empty_like(increments)
        for step in range(horizon):
            order = np.argsort(increments[:, step], kind="stable")
            rank = np.argsort(np.argsort(scores[:, step, a], kind="stable"), kind="stable")
            coupled[:, step] = increments[order[rank], step]
        out[symbol] = start[symbol] * np.exp(np.cumsum(coupled, axis=1))
    return out


def couple_paths(
    paths: Mapping[str, Floats],
    start: Mapping[str, float],
    correlation: pd.DataFrame,
    *,
    seed: int = 0,
    coupling: str = "terminal",
) -> dict[str, Floats]:
    """Re-order sample paths so returns follow a Gaussian copula (see the module docstring).

    ``coupling="terminal"`` keeps every path intact and couples end-of-horizon returns;
    ``"stepwise"`` couples every step's return and breaks within-path serial dependence.
    """
    if coupling not in ("terminal", "stepwise"):
        raise ValueError("coupling must be 'terminal' or 'stepwise'")
    symbols = list(paths)
    n = {p.shape[0] for p in paths.values()}
    if len(n) != 1:
        raise DataValidationError(
            "every asset must have the same number of sample paths to be coupled"
        )
    count = n.pop()
    corr = correlation.loc[symbols, symbols].to_numpy()
    if coupling == "stepwise":
        return _couple_stepwise(paths, start, corr, symbols, seed)
    rng = np.random.default_rng(seed)
    scores = rng.multivariate_normal(np.zeros(len(symbols)), corr, size=count)
    coupled: dict[str, Floats] = {}
    for a, symbol in enumerate(symbols):
        terminal = np.log(paths[symbol][:, -1] / start[symbol])
        order = np.argsort(terminal, kind="stable")  # path with the k-th smallest end return
        rank_of_row = np.argsort(np.argsort(scores[:, a], kind="stable"), kind="stable")
        coupled[symbol] = paths[symbol][order[rank_of_row]]
    return coupled


@dataclass(frozen=True)
class PortfolioForecast:
    """The portfolio's value paths with the provenance a risk report must carry."""

    portfolio: Portfolio
    paths: Floats  #: ``(n_paths, horizon)`` market value in the portfolio currency
    index: pd.DatetimeIndex
    as_of: pd.Timestamp
    asset_status: dict[str, str]
    model_mix: dict[str, dict[str, float]]
    correlation: pd.DataFrame | None
    dependence: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def start_value(self) -> float:
        return self.portfolio.total_value

    def to_risk_input(self, single_asset: ForecastDistribution | None = None) -> RiskInput:
        """Risk input for the portfolio.

        A one-asset portfolio needs no dependence assumption, so it keeps that forecast's
        calibration. A multi-asset portfolio is ``uncalibrated`` by construction (the
        dependence is assumed), with the reason in the notes.
        """
        if single_asset is not None:
            return RiskInput(
                name=self.portfolio.name,
                paths=self.paths,
                start_value=self.start_value,
                index=self.index,
                as_of=self.as_of,
                calibration_status=single_asset.calibration_status,
                calibration=single_asset.calibration,
                model_mix=dict(single_asset.model_mix),
                notes=self.notes,
            )
        flat = {
            f"{symbol}/{m}": w for symbol, mix in self.model_mix.items() for m, w in mix.items()
        }
        total = sum(flat.values())
        return RiskInput(
            name=self.portfolio.name,
            paths=self.paths,
            start_value=self.start_value,
            index=self.index,
            as_of=self.as_of,
            calibration_status="uncalibrated",
            calibration=None,
            model_mix={k: v / total for k, v in flat.items()},
            notes=self.notes,
        )


def aggregate_portfolio(
    portfolio: Portfolio,
    forecasts: Mapping[str, ForecastDistribution],
    *,
    correlation: pd.DataFrame | None = None,
    seed: int = 0,
    coupling: str = "terminal",
) -> PortfolioForecast:
    """Couple per-asset paths with ``correlation`` and value the portfolio along them.

    ``coupling`` is ``"terminal"`` or ``"stepwise"``; see the module docstring for what each
    preserves and what it understates.
    """
    missing = [s for s in portfolio.symbols if s not in forecasts]
    if missing:
        raise DataValidationError(f"no forecast for {missing}")
    dists = {s: forecasts[s] for s in portfolio.symbols}
    first = next(iter(dists.values()))
    for s, d in dists.items():
        d.require_paths("portfolio aggregation")
        if d.horizon != first.horizon or not d.index.equals(first.index):
            raise DataValidationError(f"{s} has a different forecast horizon or calendar")
        if d.as_of != first.as_of:
            raise DataValidationError(f"{s} was forecast as of a different time")

    paths = {s: np.asarray(d.samples) for s, d in dists.items()}
    start = {s: d.last_close for s, d in dists.items()}
    notes: list[str] = []
    if len(dists) > 1:
        if correlation is None:
            raise ModelError(
                "a multi-asset portfolio needs a correlation matrix to couple its paths"
            )
        paths = couple_paths(paths, start, correlation, seed=seed, coupling=coupling)
        if coupling == "terminal":
            dependence = "gaussian-copula (rank coupling of end-of-horizon returns)"
            notes.append(
                "Coupling is on end-of-horizon returns: cross-asset correlation at step k of H is "
                "about rho * k / H, so intermediate steps and drawdowns see less co-movement than "
                "the target. Use coupling='stepwise' if drawdowns matter more than path shape."
            )
        else:
            dependence = "gaussian-copula (rank coupling of per-step returns)"
            notes.append(
                "Coupling is on per-step returns: correlation holds at every step, but each "
                "asset's within-path serial dependence (volatility clustering) is broken."
            )
        notes.append(
            "Dependence is assumed, not calibrated: a Gaussian copula has no tail dependence, so "
            "the portfolio lower tail is likely understated. Marginals may be calibrated; the "
            "portfolio as a whole is not."
        )
    else:
        dependence = "none (single asset)"

    units = {p.symbol: p.value / start[p.symbol] for p in portfolio.positions}
    values = sum(units[s] * paths[s] for s in dists)
    return PortfolioForecast(
        portfolio=portfolio,
        paths=np.asarray(values, dtype=np.float64),
        index=first.index,
        as_of=first.as_of,
        asset_status={s: d.calibration_status for s, d in dists.items()},
        model_mix={s: dict(d.model_mix) for s, d in dists.items()},
        correlation=correlation if len(dists) > 1 else None,
        dependence=dependence,
        notes=tuple(notes),
    )


def aggregate_joint_paths(
    portfolio: Portfolio,
    paths: NDArray[np.float64],
    *,
    symbols: list[str],
    start_prices: Mapping[str, float],
    index: pd.DatetimeIndex,
    as_of: datetime,
) -> PortfolioForecast:
    """Value a portfolio along genuinely joint paths ``(n_paths, assets, horizon)``. No coupling."""
    if paths.ndim != 3 or paths.shape[1] != len(symbols):
        raise DataValidationError("paths must be (n_paths, assets, horizon)")
    units = np.array(
        [
            next(p.value for p in portfolio.positions if p.symbol == s) / start_prices[s]
            for s in symbols
        ]
    )
    values = np.einsum("a,nah->nh", units, paths)
    return PortfolioForecast(
        portfolio=portfolio,
        paths=values,
        index=index,
        as_of=as_utc(as_of),
        asset_status=dict.fromkeys(symbols, "uncalibrated"),
        model_mix={s: {"joint-simulator": 1.0} for s in symbols},
        correlation=None,
        dependence="joint paths supplied by the caller (no coupling applied)",
        notes=("joint paths were supplied; their calibration is the caller's responsibility",),
    )
