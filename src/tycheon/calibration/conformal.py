"""Split and adaptive conformal calibration of a forecast distribution.

How it works
------------
For each quantile level ``tau`` and horizon step, the *score* of a past forecast is how far
the outcome landed above the forecaster's ``tau`` quantile, ``y - q_tau`` (in log-return
space). Adding the conformal ``tau`` quantile of those scores to the forecaster's quantile
makes it cover ``tau`` of outcomes. Doing this at a grid of levels calibrates the whole
quantile function, so a central interval at any coverage, and the sample paths risk is
computed from, all come out calibrated by one mechanism. Because the shift is applied to each
tail separately it also corrects *bias*: a forecaster whose forecasts sit systematically high
gets pulled back, which a symmetric interval widening cannot do.

* :class:`SplitConformal`: scores are pooled (optionally over a recent ``window``). Marginal
  coverage is guaranteed at least nominal under exchangeability, with the finite-sample
  correction in :func:`~tycheon.calibration.metrics.conformal_index`.
* :class:`AdaptiveConformal`: ACI (Gibbs and Candes, 2021) for non-stationary data. Each
  level tracks its own effective quantile ``tau'``, nudged after every observed outcome by
  ``gamma * (tau - hit)``, so persistent miscoverage pushes it back toward target. A
  ``h``-step outcome only becomes known ``h`` bars after the forecast, so feedback is applied
  in the order outcomes were *published*, not the order forecasts were made.

Both are evaluated the same way, by replaying history: at each origin the calibrator may use
only the outcomes already known then. The most recent origins of that replay are the
**holdout** that decides ``calibrated`` versus ``stale``, so the status always rests on
genuinely out-of-sample evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from numpy.typing import NDArray

from tycheon.calibration.diagnostics import (
    DEFAULT_COVERAGES,
    CalibrationReport,
    evaluate_calibration,
)
from tycheon.calibration.metrics import conformal_index, shift_samples
from tycheon.data.asof import to_ns
from tycheon.errors import LookaheadError, ModelError
from tycheon.models.base import CalibrationInfo, CalibrationStatus, ForecastDistribution

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd

    from tycheon.calibration.scores import ScoreSet

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class Replay:
    """The outcome of replaying a calibrator over a score history."""

    levels: tuple[float, ...]
    delta_used: Floats  #: ``(n, levels, horizon)``, NaN where too few scores were known yet
    final_delta: Floats  #: ``(levels, horizon)`` to use for the next forecast
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuantileAdjustment:
    """Additive shifts of the log-return quantile function that were calibrated."""

    method: str
    levels: tuple[float, ...]
    delta: Floats  #: ``(levels, horizon)``
    horizon: int
    n_scores: int
    scores_as_of: pd.Timestamp
    n_samples: int


class Calibrator(Protocol):
    """Anything that turns a score history into a quantile adjustment by replaying it."""

    method: str

    def replay(self, scores: ScoreSet) -> Replay: ...


def _score_tensor(scores: ScoreSet) -> Floats:
    """``(n, levels, horizon)`` scores ``y - q_tau``."""
    return np.asarray(scores.realized[:, None, :] - scores.base_logq, dtype=np.float64)


class SplitConformal:
    """Pooled split conformal calibration with the finite-sample correction."""

    method = "split-conformal"

    def __init__(self, *, window: int | None = None, min_scores: int = 30) -> None:
        if window is not None and window < min_scores:
            raise ValueError("window must be at least min_scores")
        if min_scores < 2:
            raise ValueError("min_scores must be at least 2")
        self.window = window
        self.min_scores = min_scores

    def _delta(self, pool: Floats, levels: tuple[float, ...]) -> Floats:
        """Conformal shift per level from a ``(m, levels)`` pool of scores."""
        out = np.full(len(levels), np.nan)
        ordered = np.sort(pool, axis=0)
        for j, tau in enumerate(levels):
            k = conformal_index(len(pool), tau)
            if k is not None:
                out[j] = ordered[k - 1, j]
        return out

    def replay(self, scores: ScoreSet) -> Replay:
        s = _score_tensor(scores)
        n, n_levels, horizon = s.shape
        origins = to_ns(scores.origin_times)
        used = np.full((n, n_levels, horizon), np.nan)
        final = np.full((n_levels, horizon), np.nan)
        for h in range(horizon):
            outcome = scores.outcome_times[:, h]
            for i in range(n):
                known = np.flatnonzero(outcome[:i] <= origins[i])
                if self.window is not None:
                    known = known[-self.window :]
                if len(known) >= self.min_scores:
                    used[i, :, h] = self._delta(s[known, :, h], scores.levels)
            everything = np.arange(n)[-self.window :] if self.window is not None else np.arange(n)
            if len(everything) >= self.min_scores:
                final[:, h] = self._delta(s[everything, :, h], scores.levels)
        return Replay(scores.levels, used, final, {"window": self.window})


class AdaptiveConformal:
    """Adaptive conformal inference with delayed feedback, for non-stationary series.

    Args:
        gamma: Step size. Larger adapts faster and is noisier; ``0.01`` to ``0.05`` is typical.
        window: Use only the most recent ``window`` known scores for the quantile.
    """

    method = "adaptive-conformal"

    def __init__(
        self, *, gamma: float = 0.02, window: int | None = None, min_scores: int = 30
    ) -> None:
        if not 0.0 < gamma < 1.0:
            raise ValueError("gamma must be in (0, 1)")
        if window is not None and window < min_scores:
            raise ValueError("window must be at least min_scores")
        self.gamma = gamma
        self.window = window
        self.min_scores = min_scores

    @staticmethod
    def _quantile(pool: Floats, tau_eff: Floats, levels: Floats) -> Floats:
        """Per-level empirical quantile of a ``(m, levels)`` pool at effective levels."""
        m = pool.shape[0]
        ordered = np.sort(pool, axis=0)
        position = np.clip(tau_eff, 0.0, 1.0) * (m - 1)
        index = np.where(levels < 0.5, np.floor(position), np.ceil(position)).astype(int)
        return np.asarray(ordered[index, np.arange(pool.shape[1])], dtype=np.float64)

    def replay(self, scores: ScoreSet) -> Replay:
        s = _score_tensor(scores)
        n, n_levels, horizon = s.shape
        levels = np.asarray(scores.levels)
        origins = to_ns(scores.origin_times)
        used = np.full((n, n_levels, horizon), np.nan)
        final = np.full((n_levels, horizon), np.nan)
        tau_final = np.tile(levels[:, None], (1, horizon)).astype(float)

        for h in range(horizon):
            outcome = scores.outcome_times[:, h]
            order = np.argsort(outcome, kind="stable")
            tau_eff = levels.copy()
            known: list[int] = []
            pointer = 0

            def absorb(
                j: int, tau_eff: Floats = tau_eff, known: list[int] = known, h: int = h
            ) -> None:
                known.append(j)
                valid = np.isfinite(used[j, :, h])
                if valid.any():
                    hit = (s[j, :, h] <= used[j, :, h]).astype(float)
                    tau_eff[valid] += self.gamma * (levels[valid] - hit[valid])

            for i in range(n):
                while pointer < n and outcome[order[pointer]] <= origins[i]:
                    if order[pointer] < i:
                        absorb(int(order[pointer]))
                    pointer += 1
                pool_ids = known[-self.window :] if self.window is not None else known
                if len(pool_ids) >= self.min_scores:
                    used[i, :, h] = self._quantile(s[pool_ids, :, h], tau_eff, levels)
            for j in order[pointer:]:  # remaining feedback: all published by as_of
                absorb(int(j))
            pool_ids = known[-self.window :] if self.window is not None else known
            if len(pool_ids) >= self.min_scores:
                final[:, h] = self._quantile(s[pool_ids, :, h], tau_eff, levels)
            tau_final[:, h] = tau_eff

        degenerate = float(((tau_final <= 0.0) | (tau_final >= 1.0)).mean())
        return Replay(
            scores.levels,
            used,
            final,
            {"gamma": self.gamma, "window": self.window, "tau_effective": tau_final,
             "degenerate_fraction": degenerate},
        )  # fmt: skip


def _central_pairs(levels: tuple[float, ...], coverages: tuple[float, ...]) -> list[float]:
    keep = []
    for c in coverages:
        tail = (1.0 - c) / 2.0
        has_lo = any(abs(x - tail) < 1e-9 for x in levels)
        has_hi = any(abs(x - (1.0 - tail)) < 1e-9 for x in levels)
        if has_lo and has_hi:
            keep.append(c)
    return keep


class ConformalCalibrator:
    """Fit a calibrator on a score history, report how it did, and calibrate new forecasts.

    Args:
        method: :class:`SplitConformal` or :class:`AdaptiveConformal`.
        holdout_fraction, min_holdout: How many of the most recent origins are held out as the
            out-of-sample check (the larger of ``min_holdout`` and the fraction).
        reference_coverage: The nominal level whose achieved coverage decides the status.
        tolerance: Allowed gap between achieved and nominal coverage. Default
            ``max(0.05, 2 * sqrt(c * (1 - c) / n_holdout))``, i.e. about two standard errors.
    """

    def __init__(
        self,
        method: Calibrator,
        *,
        holdout_fraction: float = 0.25,
        min_holdout: int = 10,
        reference_coverage: float = 0.9,
        coverages: tuple[float, ...] = DEFAULT_COVERAGES,
        tolerance: float | None = None,
    ) -> None:
        self.method = method
        self.holdout_fraction = holdout_fraction
        self.min_holdout = min_holdout
        self.reference_coverage = reference_coverage
        self.coverages = coverages
        self.tolerance = tolerance
        self.scores: ScoreSet | None = None
        self.replay: Replay | None = None
        self.report: CalibrationReport | None = None
        self.adjustment: QuantileAdjustment | None = None

    # ----------------------------------------------------------------------- fit
    def fit(self, scores: ScoreSet) -> ConformalCalibrator:
        """Replay history, evaluate the most recent origins, and keep the final adjustment."""
        replay = self.method.replay(scores)
        holdout = max(self.min_holdout, math.ceil(self.holdout_fraction * scores.n))
        holdout = min(holdout, max(scores.n - 1, 1))
        covs = tuple(_central_pairs(scores.levels, self.coverages))
        self.report = evaluate_calibration(
            scores, replay.delta_used, holdout=holdout, method=self.method.method, coverages=covs
        )
        finite = np.asarray(np.isfinite(replay.final_delta).all(axis=1), dtype=bool)
        levels = tuple(
            level for level, ok in zip(scores.levels, finite.tolist(), strict=True) if ok
        )
        self.adjustment = QuantileAdjustment(
            method=self.method.method,
            levels=levels,
            delta=replay.final_delta[finite],
            horizon=scores.horizon,
            n_scores=scores.n,
            scores_as_of=scores.max_outcome_time,
            n_samples=scores.n_samples,
        )
        self.scores, self.replay = scores, replay
        return self

    def _tolerance(self, nominal: float, n: int) -> float:
        if self.tolerance is not None:
            return self.tolerance
        return max(0.05, 2.0 * math.sqrt(nominal * (1.0 - nominal) / max(n, 1)))

    def status(self) -> tuple[CalibrationStatus, float | None, float]:
        """``(status, achieved reference coverage, tolerance)`` from the holdout."""
        if self.report is None or self.adjustment is None:
            raise ModelError("fit the calibrator before asking for its status")
        if not self.adjustment.levels or self.report.n_holdout < self.min_holdout:
            return "uncalibrated", None, 0.0
        result = self.report.coverage_at(self.reference_coverage)
        if result is None and self.report.coverages:
            result = min(
                self.report.coverages, key=lambda c: abs(c.nominal - self.reference_coverage)
            )
        if result is None:
            return "uncalibrated", None, 0.0
        tol = self._tolerance(result.nominal, self.report.n_holdout)
        ok = abs(result.calibrated - result.nominal) <= tol
        return ("calibrated" if ok else "stale"), result.calibrated, tol

    # ----------------------------------------------------------------- calibrate
    def calibrate(self, dist: ForecastDistribution) -> ForecastDistribution:
        """Return ``dist`` with calibrated quantiles and sample paths, or unchanged if it cannot.

        Raises ``LookaheadError`` if the calibration data includes outcomes published after
        ``dist.as_of``: calibrating with the future is the leak this module exists to prevent.
        """
        if self.scores is None or self.adjustment is None or self.report is None:
            raise ModelError("fit the calibrator before calibrating a forecast")
        adj = self.adjustment
        if adj.scores_as_of > dist.as_of:
            raise LookaheadError(
                f"calibration scores include outcomes known at {adj.scores_as_of.isoformat()}, "
                f"after this forecast's as_of {dist.as_of.isoformat()}"
            )
        if dist.horizon > adj.horizon:
            raise ModelError(
                f"forecast horizon {dist.horizon} exceeds the calibrated horizon {adj.horizon}"
            )

        status, _, tol = self.status()
        notes = list(self.scores.notes)
        if status == "uncalibrated":
            diag = {**dist.metadata.diagnostics, "calibration_unavailable": self._why_not()}
            return replace(dist, metadata=replace(dist.metadata, diagnostics=diag))

        h = dist.horizon
        levels, cols = [], []
        for j, tau in enumerate(adj.levels):
            try:
                base = np.log(dist.quantile(tau) / dist.last_close)
            except ValueError:
                continue  # a quantile-only model that does not produce this level
            levels.append(tau)
            cols.append(base + adj.delta[j, :h])
        if not levels:
            return replace(dist, calibration_status="uncalibrated")
        logq = np.maximum.accumulate(np.vstack(cols), axis=0)
        quantiles = dist.last_close * np.exp(logq)

        samples = dist.samples
        if samples is not None:
            shifted = shift_samples(np.log(samples / dist.last_close), adj.levels, adj.delta[:, :h])
            samples = dist.last_close * np.exp(shifted)

        if dist.metadata.n_samples and adj.n_samples and dist.metadata.n_samples != adj.n_samples:
            notes.append(
                f"calibrated with {adj.n_samples} samples per forecast but applied to "
                f"{dist.metadata.n_samples}; quantile noise differs"
            )
        if len(levels) < len(self.scores.levels):
            notes.append(
                "tail levels with too few scores were left uncalibrated: calibrated levels are "
                + ", ".join(f"{level:g}" for level in adj.levels)
            )
        holdout_cov = {c.nominal: c.calibrated for c in self.report.coverages}
        raw_cov = {c.nominal: c.raw for c in self.report.coverages}
        info = CalibrationInfo(
            method=adj.method,
            n_scores=adj.n_scores,
            scores_as_of=adj.scores_as_of,
            holdout_n=self.report.n_holdout,
            holdout_coverage=holdout_cov,
            raw_holdout_coverage=raw_cov,
            tolerance=tol,
            calibrated_levels=adj.levels,
            notes=tuple(notes),
        )
        diag = {**dist.metadata.diagnostics, "calibrated_by": adj.method}
        return replace(
            dist,
            quantile_levels=tuple(levels),
            quantiles=quantiles,
            samples=samples,
            calibration_status=status,
            calibration=info,
            metadata=replace(dist.metadata, diagnostics=diag),
        )

    def _why_not(self) -> str:
        if self.report is None or self.adjustment is None:
            raise ModelError("fit the calibrator first")
        if not self.adjustment.levels:
            return f"too few scores ({self.adjustment.n_scores}) to calibrate any level"
        return f"only {self.report.n_holdout} evaluable holdout origins (need {self.min_holdout})"


def calibrate(
    dist: ForecastDistribution,
    scores: ScoreSet,
    *,
    method: str = "split",
    **kwargs: Any,
) -> ForecastDistribution:
    """One-call convenience: fit a calibrator on ``scores`` and calibrate ``dist``.

    ``method`` is ``"split"`` or ``"adaptive"``; keyword arguments go to the method
    (``window``, ``gamma``, ``min_scores``).
    """
    chosen: Calibrator = (
        AdaptiveConformal(**kwargs) if method == "adaptive" else SplitConformal(**kwargs)
    )
    return ConformalCalibrator(chosen).fit(scores).calibrate(dist)
