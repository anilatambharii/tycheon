"""Regime-aware ensemble: weight forecasters by recent out-of-sample loss, per regime.

Different forecasters win in different conditions. The router measures each candidate's
**out-of-sample** loss (the mean pinball score over a shared quantile grid, a proper score that
works for sample-path and quantile-only models alike) on past origins, groups the origins by
the regime that prevailed *at that origin*, and weights each candidate by how it did recently
in the regime the market is in now.

* The **random walk is always a candidate**: a router that cannot fall back to "do nothing"
  can only add risk.
* Weights come from origins whose outcomes were already published when the weights are used,
  so nothing after ``as_of`` leaks in (:meth:`RegimeRouter.replay` re-runs this online over
  history, and is also how the ensemble gets honest scores to be calibrated on).
* Weights are shrunk toward equal, so a short lucky streak cannot give one model everything.

The combined forecast has two modes. ``"pool"`` (default) pools sample paths from the
path-capable members in proportion to their weights, so risk measures keep working;
quantile-only members (TimesFM, Chronos) cannot supply paths and sit out of the pool.
``"quantile"`` averages quantiles of every member (Vincentisation), which includes the
quantile-only models but returns no paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from tycheon.calibration.metrics import quantile_score
from tycheon.calibration.scores import ScoreSet
from tycheon.data.asof import as_utc, to_ns
from tycheon.errors import DataValidationError, ModelError
from tycheon.models.base import (
    DEFAULT_QUANTILE_LEVELS,
    BaseForecaster,
    ForecastDistribution,
    PreparedHistory,
    RawForecast,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from tycheon.models.base import Forecaster
    from tycheon.routing.regimes import RegimeDetector

Floats = NDArray[np.float64]
RANDOM_WALK_ID = "random-walk"


@dataclass(frozen=True)
class RouterWeights:
    """Per-regime candidate weights, with the evidence they came from."""

    model_ids: tuple[str, ...]
    by_regime: Mapping[int, Mapping[str, float]]
    overall: Mapping[str, float]
    n_by_regime: Mapping[int, int]
    mean_loss_by_regime: Mapping[int, Mapping[str, float]]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def for_regime(self, regime: int) -> Mapping[str, float]:
        """Weights for ``regime``, falling back to the overall weights if it is unseen."""
        return self.by_regime.get(regime, self.overall)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_ids": list(self.model_ids),
            "by_regime": {str(r): dict(w) for r, w in self.by_regime.items()},
            "overall": dict(self.overall),
            "n_by_regime": {str(r): n for r, n in self.n_by_regime.items()},
            "mean_loss_by_regime": {str(r): dict(v) for r, v in self.mean_loss_by_regime.items()},
            "notes": list(self.notes),
        }


def labels_for_scores(
    detector: RegimeDetector, bars: pd.DataFrame, scores: ScoreSet
) -> NDArray[np.int64]:
    """The regime at each scored origin, read causally from ``bars`` (known at those origins)."""
    available = to_ns(pd.DatetimeIndex(pd.to_datetime(bars["available_at"], utc=True)))
    positions = np.searchsorted(available, to_ns(scores.origin_times))
    labels: NDArray[np.int64] = detector.labels(bars)[positions]
    return labels


def _common_levels(member_scores: Mapping[str, ScoreSet]) -> tuple[float, ...]:
    sets = [set(s.levels) for s in member_scores.values()]
    common = set.intersection(*sets)
    return tuple(sorted(common))


def _check_aligned(member_scores: Mapping[str, ScoreSet]) -> ScoreSet:
    first = next(iter(member_scores.values()))
    for name, s in member_scores.items():
        if (
            s.n != first.n
            or not s.origin_times.equals(first.origin_times)
            or s.horizon != first.horizon
        ):
            raise DataValidationError(f"{name} was not scored at the same origins as the others")
    return first


class RegimeRouter:
    """Turns member score sets and regime labels into per-regime weights.

    Args:
        window: Most recent in-regime origins used for each regime's weights.
        sensitivity: How hard relative loss differences are punished: a model whose mean loss
            is ``d`` (as a fraction) above the best gets weight ``exp(-sensitivity * d)``.
        shrinkage: Share of weight pulled toward equal weights.
        min_per_regime: Fewer in-regime origins than this and that regime uses the overall
            weights.
    """

    def __init__(
        self,
        *,
        window: int = 60,
        sensitivity: float = 8.0,
        shrinkage: float = 0.15,
        min_per_regime: int = 8,
    ) -> None:
        if not 0.0 <= shrinkage < 1.0:
            raise ValueError("shrinkage must be in [0, 1)")
        self.window = window
        self.sensitivity = sensitivity
        self.shrinkage = shrinkage
        self.min_per_regime = min_per_regime

    # ---------------------------------------------------------------- internals
    def _origin_losses(self, member_scores: Mapping[str, ScoreSet]) -> tuple[list[str], Floats]:
        _check_aligned(member_scores)
        levels = _common_levels(member_scores)
        if not levels:
            raise DataValidationError("the candidates share no quantile levels to be compared on")
        ids = list(member_scores)
        columns = []
        for name in ids:
            s = member_scores[name]
            keep = [s.levels.index(level) for level in levels]
            score = quantile_score(levels, s.base_logq[:, keep, :], s.realized)  # (n, H)
            columns.append(score.mean(axis=1))
        return ids, np.stack(columns, axis=1)

    def _weights(self, losses: Floats) -> Floats:
        mean = losses.mean(axis=0)
        floor = float(np.min(mean))
        relative = (mean - floor) / max(floor, 1e-12)
        w = np.exp(-self.sensitivity * relative)
        w /= w.sum()
        return np.asarray((1.0 - self.shrinkage) * w + self.shrinkage / len(w), dtype=np.float64)

    # --------------------------------------------------------------------- fit
    def fit(
        self, member_scores: Mapping[str, ScoreSet], labels: NDArray[np.int64]
    ) -> RouterWeights:
        """Per-regime weights from all scored origins (all of them published by ``as_of``)."""
        if RANDOM_WALK_ID not in member_scores:
            raise ModelError(f"{RANDOM_WALK_ID} must always be a candidate")
        ids, losses = self._origin_losses(member_scores)
        n = len(labels)
        if losses.shape[0] != n:
            raise DataValidationError("labels must have one entry per scored origin")
        notes: list[str] = []
        overall = dict(zip(ids, self._weights(losses[-2 * self.window :]), strict=True))
        by_regime: dict[int, dict[str, float]] = {}
        n_by: dict[int, int] = {}
        loss_by: dict[int, dict[str, float]] = {}
        for regime in sorted({int(r) for r in labels if r >= 0}):
            idx = np.flatnonzero(labels == regime)
            n_by[regime] = len(idx)
            idx = idx[-self.window :]
            if len(idx) < self.min_per_regime:
                notes.append(f"regime {regime}: only {len(idx)} origins, using overall weights")
                continue
            by_regime[regime] = dict(zip(ids, self._weights(losses[idx]), strict=True))
            loss_by[regime] = dict(zip(ids, losses[idx].mean(axis=0), strict=True))
        return RouterWeights(tuple(ids), by_regime, overall, n_by, loss_by, tuple(notes))

    # ------------------------------------------------------------------ replay
    def replay(
        self, member_scores: Mapping[str, ScoreSet], labels: NDArray[np.int64], *, seed: int = 0
    ) -> ScoreSet:
        """The ensemble's own score history, with weights chosen *online* at every origin.

        At origin ``i`` the weights use only origins whose outcomes were published by then, in
        the same regime as ``i``. Sample paths are pooled (if every member has them), otherwise
        quantiles are averaged. The result is an honest record to calibrate the ensemble on.
        """
        first = _check_aligned(member_scores)
        ids, losses = self._origin_losses(member_scores)
        levels = _common_levels(member_scores)
        n, horizon = first.n, first.horizon
        origins = to_ns(first.origin_times)
        done_by = first.outcome_times.max(axis=1)
        have_paths = all(s.samples is not None for s in member_scores.values())
        s_out = min(s.n_samples for s in member_scores.values())

        logq = np.empty((n, len(levels), horizon))
        pooled = np.empty((n, s_out, horizon)) if have_paths else None
        rng = np.random.default_rng(seed)
        for i in range(n):
            known = np.flatnonzero(done_by[:i] <= origins[i])
            same = (
                known[labels[known] == labels[i]][-self.window :] if labels[i] >= 0 else known[:0]
            )
            if len(same) >= self.min_per_regime:
                w = self._weights(losses[same])
            elif len(known) > 0:
                w = self._weights(losses[known[-2 * self.window :]])
            else:
                w = np.full(len(ids), 1.0 / len(ids))
            if have_paths and pooled is not None:
                per_member = [_paths_at(member_scores[m], i) for m in ids]
                pooled[i] = _pool_log_samples(per_member, w, s_out, rng)
                logq[i] = np.quantile(pooled[i], levels, axis=0)
            else:
                for m, wm in zip(ids, w, strict=True):
                    keep = [member_scores[m].levels.index(level) for level in levels]
                    logq[i] += wm * member_scores[m].base_logq[i][keep]
        return ScoreSet(
            model_id="regime-ensemble",
            horizon=horizon,
            levels=levels,
            origin_times=first.origin_times,
            outcome_times=first.outcome_times,
            base_logq=logq,
            realized=first.realized,
            samples=pooled,
            n_samples=s_out,
            stride=first.stride,
            notes=first.notes,
        )


def _paths_at(scores: ScoreSet, i: int) -> Floats:
    """Sample paths of origin ``i``; the caller has already checked the set has paths."""
    if scores.samples is None:
        raise ModelError(f"{scores.model_id} has no sample paths")
    paths: Floats = scores.samples[i]
    return paths


def _allocate(weights: Floats, total: int) -> NDArray[np.int64]:
    """Integer counts summing to ``total``, proportional to ``weights`` (largest remainder)."""
    raw = weights * total
    counts = np.floor(raw).astype(np.int64)
    short = total - int(counts.sum())
    if short > 0:
        counts[np.argsort(-(raw - counts))[:short]] += 1
    return counts


def _pool_log_samples(
    member_samples: list[Floats], weights: Floats, total: int, rng: np.random.Generator
) -> Floats:
    """A linear opinion pool: ``total`` paths drawn from members in proportion to weight."""
    counts = _allocate(weights, total)
    parts = []
    for samples, count in zip(member_samples, counts, strict=True):
        if count:
            pick = rng.choice(
                samples.shape[0], size=int(min(count, samples.shape[0])), replace=False
            )
            parts.append(samples[pick])
    return np.concatenate(parts, axis=0)


class EnsembleForecaster(BaseForecaster):
    """Combine forecasters with the weights of the regime the market is in now.

    The random walk must be among ``members``. See the module docstring for ``mode``.
    """

    model_id = "regime-ensemble"
    model_card = "docs/models/regime-ensemble.md"
    min_history = 2

    def __init__(
        self,
        members: Mapping[str, Forecaster],
        detector: RegimeDetector,
        weights: RouterWeights,
        *,
        mode: str = "pool",
        prune: float = 0.01,
        seed: int | None = 0,
    ) -> None:
        super().__init__(seed=seed)
        if RANDOM_WALK_ID not in members:
            raise ModelError(f"{RANDOM_WALK_ID} must always be a candidate")
        if mode not in ("pool", "quantile"):
            raise ValueError("mode must be 'pool' or 'quantile'")
        usable = [m for m in members if mode == "quantile" or members[m].supports_paths]
        if not usable:
            raise ModelError("no member can supply sample paths; use mode='quantile'")
        self.members = dict(members)
        self.detector = detector
        self.weights = weights
        self.mode = mode
        self.prune = prune
        self.supports_paths = mode == "pool"
        unscored = [m for m in usable if m not in weights.model_ids]
        if unscored:
            raise ModelError(
                f"members {unscored} were not scored by the router, so they have no weight; "
                "score every member that will be used (a silently ignored member is worse than "
                "an error)"
            )
        self._usable = usable

    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:  # pragma: no cover - predict() is overridden
        raise NotImplementedError

    def current_weights(self, history: pd.DataFrame) -> tuple[int, dict[str, float]]:
        """The regime now, and the renormalised weights of the usable members in it."""
        regime = int(self.detector.labels(history)[-1])
        base = self.weights.for_regime(regime)
        raw = np.array([base.get(m, 0.0) for m in self._usable], dtype=float)
        raw[raw < self.prune] = 0.0
        if raw.sum() <= 0:
            raw = np.ones(len(self._usable))
        raw /= raw.sum()
        return regime, dict(zip(self._usable, raw, strict=True))

    def predict(
        self,
        history: pd.DataFrame,
        horizon: int,
        n_samples: int,
        as_of: datetime,
        *,
        seed: int | None = None,
    ) -> ForecastDistribution:
        as_of_ts = as_utc(as_of)
        regime, w = self.current_weights(history)
        used = {m: wt for m, wt in w.items() if wt > 0}
        dists = {
            m: self.members[m].predict(history, horizon, n_samples, as_of_ts, seed=seed)
            for m in used
        }
        template = dists[next(iter(dists))]
        rng = np.random.default_rng(self.seed if seed is None else seed)
        weights_arr = np.array([used[m] for m in dists])

        if self.mode == "pool":
            last = template.last_close
            log_paths = [
                np.log(d.require_paths("regime-ensemble pooling") / last) for d in dists.values()
            ]
            pooled = _pool_log_samples(log_paths, weights_arr, n_samples, rng)
            pooled_prices: Floats = last * np.exp(pooled)
            samples: Floats | None = pooled_prices
            levels = DEFAULT_QUANTILE_LEVELS
            quantiles = np.quantile(pooled_prices, levels, axis=0)
        else:
            shared = sorted(set.intersection(*[set(d.quantile_levels) for d in dists.values()]))
            if not shared:
                raise ModelError("the members share no quantile levels")
            samples, levels = None, tuple(shared)
            averaged = np.zeros((len(shared), template.horizon))
            for wt, d in zip(weights_arr, dists.values(), strict=True):  # Vincentisation
                averaged += wt * np.vstack([d.quantile(level) for level in shared])
            quantiles = np.maximum.accumulate(averaged, axis=0)

        diagnostics = {
            "regime": regime,
            "weights": {m: float(wt) for m, wt in used.items()},
            "mode": self.mode,
            "excluded_members": [m for m in self.members if m not in self._usable],
        }
        return replace(
            template,
            quantile_levels=tuple(levels),
            quantiles=np.asarray(quantiles, dtype=np.float64),
            samples=None if samples is None else np.asarray(samples, dtype=np.float64),
            model_mix={m: float(wt) for m, wt in used.items()},
            model_card=self.model_card,
            calibration_status="uncalibrated",
            calibration=None,
            extras={},
            metadata=replace(
                template.metadata,
                model_id=self.model_id,
                n_samples=0 if samples is None else int(samples.shape[0]),
                diagnostics=diagnostics,
            ),
        )
