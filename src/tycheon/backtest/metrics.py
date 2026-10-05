"""Evaluation metrics on a walk-forward :class:`~tycheon.calibration.scores.ScoreSet`.

Everything is computed in log-return space relative to the origin's last close, over the
whole horizon or at its end (the headline). Every model is read against the **random walk**,
whose point forecast is "no change" (a log return of zero):

* ``mase`` is the model's MAE divided by the random walk's MAE on the same origins, so a
  value below 1 beats doing nothing (this is the MASE with the naive no-change forecast as
  the scaling forecast).
* :func:`diebold_mariano` tests whether the difference in loss to the random walk is
  distinguishable from zero, with the Harvey-Leybourne-Newbold small-sample correction.

Where a metric needs sample paths (CRPS) and the model has none, it is approximated from the
quantile grid and flagged ``crps_approximate``; it is never silently mixed with the exact one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import stats

from tycheon.calibration.metrics import crps_samples, pinball
from tycheon.calibration.scores import ScoreSet
from tycheon.data.asof import to_ns

Floats = NDArray[np.float64]

#: Central interval coverages reported by default.
COVERAGES: tuple[float, ...] = (0.5, 0.8, 0.9)

#: How much weaker than the random walk before the verdict says "worse".
SIGNIFICANCE = 0.05


# --------------------------------------------------------------------------- point
def _end_median(s: ScoreSet) -> Floats:
    j = (
        s.levels.index(0.5)
        if 0.5 in s.levels
        else int(np.argmin(np.abs(np.asarray(s.levels) - 0.5)))
    )
    return np.asarray(s.base_logq[:, j, -1], dtype=np.float64)


def _end_realized(s: ScoreSet) -> Floats:
    return np.asarray(s.realized[:, -1], dtype=np.float64)


def mae(s: ScoreSet) -> float:
    """Mean absolute error of the median forecast of the horizon-end log return."""
    return float(np.mean(np.abs(_end_median(s) - _end_realized(s))))


def rmse(s: ScoreSet) -> float:
    """Root mean squared error of the median forecast of the horizon-end log return."""
    return float(np.sqrt(np.mean((_end_median(s) - _end_realized(s)) ** 2)))


def mase(s: ScoreSet) -> float:
    """MAE over the no-change forecast's MAE on the same origins (below 1 beats the walk)."""
    naive = float(np.mean(np.abs(_end_realized(s))))
    return mae(s) / naive if naive > 0 else math.nan


def directional_accuracy(s: ScoreSet) -> float:
    """Share of origins where the median forecast has the sign of the realised return.

    Origins where either is exactly zero are excluded (a flat forecast makes no call).
    """
    pred, real = _end_median(s), _end_realized(s)
    keep = (pred != 0) & (real != 0)
    if not keep.any():
        return math.nan
    return float(np.mean(np.sign(pred[keep]) == np.sign(real[keep])))


def information_coefficient(s: ScoreSet, *, rank: bool = False) -> float:
    """Correlation between forecast and realised horizon-end returns across origins.

    ``rank=True`` gives the Spearman rank IC. A walk-forward IC on one series is a time-series
    IC; it is NaN when the forecast is constant (as the random walk's is).
    """
    pred, real = _end_median(s), _end_realized(s)
    if len(pred) < 3 or np.ptp(pred) == 0 or np.ptp(real) == 0:
        return math.nan
    if rank:
        return float(stats.spearmanr(pred, real).statistic)
    return float(np.corrcoef(pred, real)[0, 1])


# -------------------------------------------------------------------- distribution
def crps_per_origin(s: ScoreSet) -> tuple[Floats, bool]:
    """CRPS of the horizon-end return at each origin, and whether it is approximate.

    Exact (fair, from samples) when the model has sample paths. Otherwise ``2 * mean pinball``
    over the recorded quantile levels, a quantile-grid approximation of the CRPS.
    """
    y = _end_realized(s)
    if s.samples is not None:
        end = crps_samples(s.samples[:, :, -1:], y[:, None])[:, 0]
        return np.asarray(end, dtype=np.float64), False
    return grid_crps(s), True


def grid_crps(s: ScoreSet) -> Floats:
    """CRPS of the horizon-end return from the quantile grid: ``2 * integral of pinball dtau``.

    The integral is taken by the trapezoid rule over the recorded levels, with the pinball loss
    taken as zero at ``tau = 0`` and ``1``. Tails beyond the outermost level are therefore
    ignored, so this slightly *under*-states the CRPS (by roughly 2% with the default
    1%..99% grid, more with a coarser one). It is used only where a model has no sample paths.
    """
    y = _end_realized(s)
    taus = np.concatenate([[0.0], np.asarray(s.levels), [1.0]])
    loss = np.zeros((s.n, len(taus)))
    for j, level in enumerate(s.levels, start=1):
        loss[:, j] = pinball(level, s.base_logq[:, j - 1, -1], y)
    return np.asarray(2.0 * np.trapezoid(loss, taus, axis=1), dtype=np.float64)


def interval_stats(s: ScoreSet, coverage: float) -> tuple[float, float] | None:
    """Empirical coverage and mean width (log units) of the horizon-end central interval."""
    lo_level, hi_level = (1 - coverage) / 2, 1 - (1 - coverage) / 2
    levels = np.asarray(s.levels)
    lo_i = np.flatnonzero(np.isclose(levels, lo_level))
    hi_i = np.flatnonzero(np.isclose(levels, hi_level))
    if not len(lo_i) or not len(hi_i):
        return None
    lo, hi = s.base_logq[:, lo_i[0], -1], s.base_logq[:, hi_i[0], -1]
    y = _end_realized(s)
    return float(np.mean((y >= lo) & (y <= hi))), float(np.mean(hi - lo))


def forecast_variance(s: ScoreSet) -> Floats | None:
    """Variance of the horizon-end log return implied by the sample paths (``None`` if none)."""
    if s.samples is None:
        return None
    return np.asarray(s.samples[:, :, -1].var(axis=1, ddof=1), dtype=np.float64)


def realized_variance(s: ScoreSet) -> Floats:
    """Sum of squared one-bar log returns over the horizon: a realised-variance proxy."""
    steps = np.diff(np.concatenate([np.zeros((s.n, 1)), s.realized], axis=1), axis=1)
    return np.asarray((steps**2).sum(axis=1), dtype=np.float64)


def qlike(forecast_var: Floats, realized_var: Floats) -> float:
    """Mean QLIKE loss, ``r/f - log(r/f) - 1``: robust to noise in the variance proxy.

    Lower is better, zero is a perfect forecast. Origins with a non-positive input are dropped.
    """
    keep = (forecast_var > 0) & (realized_var > 0)
    if not keep.any():
        return math.nan
    ratio = realized_var[keep] / forecast_var[keep]
    return float(np.mean(ratio - np.log(ratio) - 1.0))


# --------------------------------------------------------------- Diebold-Mariano
@dataclass(frozen=True)
class DieboldMariano:
    """Result of a Diebold-Mariano test of model against benchmark.

    ``mean_diff`` is the mean of ``loss_model - loss_benchmark``: negative means the model is
    better. ``p_two_sided`` tests equality; ``p_model_better`` is the one-sided p-value for
    "the model has lower expected loss".
    """

    statistic: float
    p_two_sided: float
    p_model_better: float
    mean_diff: float
    n: int
    lags: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "statistic": self.statistic,
            "p_two_sided": self.p_two_sided,
            "p_model_better": self.p_model_better,
            "mean_diff": self.mean_diff,
            "n": self.n,
            "lags": self.lags,
        }


def diebold_mariano(loss_model: Floats, loss_benchmark: Floats, *, lags: int = 0) -> DieboldMariano:
    """Two-sided Diebold-Mariano test with the Harvey-Leybourne-Newbold correction.

    Args:
        lags: Autocovariance lags to include in the long-run variance (``horizon_eff - 1`` for
            overlapping ``horizon_eff``-step forecasts; 0 when origins do not overlap).
    """
    d = np.asarray(loss_model, dtype=np.float64) - np.asarray(loss_benchmark, dtype=np.float64)
    n = len(d)
    mean = float(d.mean()) if n else math.nan
    if n < 3:
        return DieboldMariano(math.nan, math.nan, math.nan, mean, n, lags)
    centred = d - mean
    gamma = [float(np.dot(centred[k:], centred[: n - k]) / n) for k in range(lags + 1)]
    var = gamma[0] + 2.0 * sum(gamma[1:])  # rectangular truncation at h-1, as Diebold-Mariano
    if var <= 0 and lags:
        # the rectangular estimate can go negative in small samples; Bartlett cannot
        var = gamma[0] + 2.0 * sum((1.0 - k / (lags + 1)) * g for k, g in enumerate(gamma) if k)
    if var <= 0:
        # identical losses: no evidence either way
        return DieboldMariano(0.0, 1.0, 0.5, mean, n, lags)
    stat = mean / math.sqrt(var / n)
    h = lags + 1
    correction = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    stat *= correction
    dist = stats.t(df=n - 1)
    p_two = float(2 * dist.sf(abs(stat)))
    p_better = float(dist.cdf(stat))
    return DieboldMariano(float(stat), p_two, p_better, mean, n, lags)


def overlap_lags(horizon: int, stride: int) -> int:
    """Autocovariance lags needed when origins ``stride`` apart forecast ``horizon`` ahead."""
    return max(0, math.ceil(horizon / stride) - 1)


# ---------------------------------------------------------------------- summary
@dataclass(frozen=True)
class ModelEvaluation:
    """All headline metrics for one model on one set of origins, with DM tests vs the walk."""

    model_id: str
    n_origins: int
    horizon: int
    mae: float
    rmse: float
    mase: float
    directional_accuracy: float
    ic: float
    rank_ic: float
    crps: float
    crps_approximate: bool
    coverage: dict[float, tuple[float, float]]
    qlike: float | None
    dm: dict[str, DieboldMariano]
    is_benchmark: bool = False

    @property
    def verdict(self) -> str:
        """Plain-language read of the squared-error and CRPS tests against the random walk."""
        if self.is_benchmark:
            return "benchmark"
        tests = [
            t
            for t in (self.dm.get("squared_error"), self.dm.get("crps"))
            if t is not None and not math.isnan(t.p_two_sided)
        ]
        if not tests:
            return "not tested"
        if all(t.p_model_better < SIGNIFICANCE for t in tests):
            return "beats the random walk"
        if all(1 - t.p_model_better < SIGNIFICANCE for t in tests):
            return "worse than the random walk"
        return "indistinguishable from the random walk"

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "n_origins": self.n_origins,
            "horizon": self.horizon,
            "mae": self.mae,
            "rmse": self.rmse,
            "mase": self.mase,
            "directional_accuracy": self.directional_accuracy,
            "ic": self.ic,
            "rank_ic": self.rank_ic,
            "crps": self.crps,
            "crps_approximate": self.crps_approximate,
            "coverage": {
                f"{c:g}": {"achieved": v[0], "mean_width": v[1]} for c, v in self.coverage.items()
            },
            "qlike": self.qlike,
            "diebold_mariano_vs_random_walk": {k: v.to_dict() for k, v in self.dm.items()},
            "verdict": self.verdict,
            "is_benchmark": self.is_benchmark,
        }


def _squared_error(s: ScoreSet) -> Floats:
    return (_end_median(s) - _end_realized(s)) ** 2


def evaluate(
    model: ScoreSet,
    benchmark: ScoreSet,
    *,
    coverages: tuple[float, ...] = COVERAGES,
) -> ModelEvaluation:
    """Metrics for ``model`` and Diebold-Mariano tests against ``benchmark`` (the random walk).

    Both sets must cover the same origins; that is checked, because a comparison on different
    origins measures the origins, not the models.
    """
    if model.n != benchmark.n or not model.origin_times.equals(benchmark.origin_times):
        raise ValueError("model and benchmark were not scored at the same origins")
    crps_m, approx_m = crps_per_origin(model)
    crps_b, approx_b = crps_per_origin(benchmark)
    if approx_m != approx_b:
        # compare like with like: fall back to the quantile-grid CRPS for both
        crps_m = grid_crps(model)
        crps_b = grid_crps(benchmark)
        approx_m = True
    lags = overlap_lags(model.horizon, model.stride)
    dm = {
        "squared_error": diebold_mariano(
            _squared_error(model), _squared_error(benchmark), lags=lags
        ),
        "crps": diebold_mariano(crps_m, crps_b, lags=lags),
    }
    fv, rv = forecast_variance(model), realized_variance(model)
    cover = {}
    for c in coverages:
        got = interval_stats(model, c)
        if got is not None:
            cover[c] = got
    return ModelEvaluation(
        model_id=model.model_id,
        n_origins=model.n,
        horizon=model.horizon,
        mae=mae(model),
        rmse=rmse(model),
        mase=mase(model),
        directional_accuracy=directional_accuracy(model),
        ic=information_coefficient(model),
        rank_ic=information_coefficient(model, rank=True),
        crps=float(crps_m.mean()),
        crps_approximate=approx_m,
        coverage=cover,
        qlike=None if fv is None else qlike(fv, rv),
        dm=dm,
        is_benchmark=model.model_id == benchmark.model_id,
    )


def pool_scores(sets: list[ScoreSet], model_id: str | None = None) -> ScoreSet:
    """Stack the walk-forward records of several series into one, ordered by origin time.

    Used to evaluate a model across a universe. The same ordering is produced for every model
    scored at the same origins, so a pooled model and a pooled benchmark stay aligned. Pooled
    Diebold-Mariano tests treat series as independent; correlated series make them too
    confident, which the methodology page says.
    """
    if not sets:
        raise ValueError("nothing to pool")
    first = sets[0]
    for other in sets[1:]:
        if (other.horizon, other.levels, other.stride) != (
            first.horizon,
            first.levels,
            first.stride,
        ):
            raise ValueError("series must share horizon, levels and stride to be pooled")
    origins = np.concatenate([to_ns(s.origin_times) for s in sets])
    order = np.argsort(origins, kind="stable")
    samples = None
    if all(s.samples is not None for s in sets):
        samples = np.concatenate([s.samples for s in sets if s.samples is not None])[order]
    return ScoreSet(
        model_id=model_id or first.model_id,
        horizon=first.horizon,
        levels=first.levels,
        origin_times=pd.DatetimeIndex([t for s in sets for t in s.origin_times])[order],
        outcome_times=np.concatenate([s.outcome_times for s in sets])[order],
        base_logq=np.concatenate([s.base_logq for s in sets])[order],
        realized=np.concatenate([s.realized for s in sets])[order],
        samples=samples,
        n_samples=first.n_samples,
        stride=first.stride,
        notes=first.notes,
    )
