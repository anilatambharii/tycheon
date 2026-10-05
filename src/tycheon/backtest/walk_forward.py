"""Rolling-origin walk-forward evaluation with refit schedules and an embargo.

The engine places forecast origins in ``folds`` consecutive test windows ending at the last
bar whose outcome is fully known. A forecaster is *built* by a factory from training history
that ends ``embargo`` bars before the first origin it will serve, then asked for forecasts at
each origin using only what had been published by that origin. Plain forecasters need no
fitting (a constant factory); the Tycheon ensemble and the calibrated forecaster refit their
router and calibrator from the training history at every refit.

Every ``predict`` is wrapped in :class:`~tycheon.backtest.guards.GuardedForecaster`, and every
training history is checked against the embargo, so lookahead raises instead of inflating a
score. The output is a :class:`~tycheon.calibration.scores.ScoreSet`, the same record the
calibration layer uses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from tycheon.backtest.guards import GuardedForecaster, assert_fit_data_precedes, assert_known_by
from tycheon.calibration.scores import DEFAULT_LEVELS, scoreset_from_forecasts
from tycheon.data.asof import AVAILABLE_AT, to_ns
from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from tycheon.calibration.scores import ScoreSet
    from tycheon.models.base import ForecastDistribution, Forecaster

#: Builds a forecaster from the training history and the moment that history was known.
ForecasterFactory = Callable[[pd.DataFrame, pd.Timestamp], "Forecaster"]


def static(forecaster: Forecaster) -> ForecasterFactory:
    """A factory for a forecaster that needs no fitting from the history."""

    def make(history: pd.DataFrame, as_of: pd.Timestamp) -> Forecaster:  # noqa: ARG001
        return forecaster

    return make


@dataclass(frozen=True)
class WalkForwardConfig:
    """How a walk-forward run is laid out.

    Attributes:
        horizon: Bars ahead.
        folds: Consecutive test windows, newest last.
        test_window: Bars spanned by each fold's origins.
        train_window: Cap on the bars given to the factory (``None``: everything before the
            embargo).
        embargo: Bars between the end of the training history and the first origin it serves.
        stride: Bars between origins (default ``horizon``: horizons do not overlap).
        refit: ``"fold"`` (rebuild the forecaster at each fold), ``"never"`` (build once, for
            the first fold) or ``"every:K"`` (every K origins).
        n_samples: Paths per forecast.
        max_history: Cap on the history given to ``predict``.
        min_history: Fewest bars a training history or a forecast context may have.
    """

    horizon: int
    folds: int = 3
    test_window: int = 63
    train_window: int | None = None
    embargo: int = 5
    stride: int | None = None
    refit: str = "fold"
    n_samples: int = 50
    max_history: int | None = None
    min_history: int = 60
    seed: int = 0
    levels: tuple[float, ...] = DEFAULT_LEVELS

    def __post_init__(self) -> None:
        if self.horizon < 1 or self.folds < 1 or self.test_window < 1:
            raise ValueError("horizon, folds and test_window must be positive")
        if self.embargo < 0:
            raise ValueError("embargo must not be negative")
        if self.stride is not None and self.stride < 1:
            raise ValueError("stride must be at least 1")
        self._refit_every()

    @property
    def step(self) -> int:
        return self.horizon if self.stride is None else self.stride

    def _refit_every(self) -> int | None:
        """Origins per refit group; ``None`` means one group per fold."""
        if self.refit in ("fold", "never"):
            return None
        kind, _, number = self.refit.partition(":")
        if kind != "every" or not number.isdigit() or int(number) < 1:
            raise ValueError(f"refit must be 'fold', 'never' or 'every:K', got {self.refit!r}")
        return int(number)

    @property
    def origins_per_fold(self) -> int:
        return max(1, self.test_window // self.step)


@dataclass(frozen=True)
class WalkForwardResult:
    """What a walk-forward run produced, with the evidence that it was clean."""

    scores: ScoreSet
    fold_of_origin: NDArray[np.int64]
    refits: tuple[dict[str, object], ...]
    guard_checks: int
    warnings: tuple[str, ...] = ()
    seconds: float = 0.0
    notes: tuple[str, ...] = field(default=())

    @property
    def n_folds(self) -> int:
        return int(np.max(self.fold_of_origin)) + 1 if len(self.fold_of_origin) else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.scores.model_id,
            "n_origins": self.scores.n,
            "n_folds": self.n_folds,
            "refits": list(self.refits),
            "guard_checks": self.guard_checks,
            "warnings": list(self.warnings),
            "seconds": round(self.seconds, 3),
        }


def plan_origins(n_bars: int, config: WalkForwardConfig) -> tuple[list[int], list[int]]:
    """Bar positions of the origins (oldest first) and the fold index of each."""
    per_fold = config.origins_per_fold
    total = per_fold * config.folds
    last = n_bars - 1 - config.horizon
    origins = [last - k * config.step for k in range(total)][::-1]
    folds = [k // per_fold for k in range(total)]
    return origins, folds


def _groups(origins: list[int], folds: list[int], config: WalkForwardConfig) -> list[list[int]]:
    """Indices (into ``origins``) served by each forecaster build."""
    if config.refit == "never":
        return [list(range(len(origins)))]
    every = config._refit_every()
    if every is None:
        by_fold: dict[int, list[int]] = {}
        for idx, fold in enumerate(folds):
            by_fold.setdefault(fold, []).append(idx)
        return [by_fold[f] for f in sorted(by_fold)]
    return [list(range(s, min(s + every, len(origins)))) for s in range(0, len(origins), every)]


def walk_forward(
    factory: ForecasterFactory,
    bars: pd.DataFrame,
    config: WalkForwardConfig,
) -> WalkForwardResult:
    """Run ``factory`` through the walk-forward schedule on one symbol's bars.

    Raises :class:`~tycheon.errors.LookaheadError` if any check fails; raises
    :class:`~tycheon.errors.DataValidationError` if the series is too short for the layout.
    """
    started = time.perf_counter()
    close = bars["close"].to_numpy(dtype=np.float64)
    available = pd.DatetimeIndex(pd.to_datetime(bars[AVAILABLE_AT], utc=True))
    available_ns = to_ns(available)
    origins, folds = plan_origins(len(bars), config)
    first_fit_end = origins[0] - config.embargo
    if first_fit_end + 1 < config.min_history:
        needed = origins[0] - first_fit_end + config.min_history
        raise DataValidationError(
            f"{len(bars)} bars are too few: the first fold needs about {needed} more bars of "
            f"training history (min_history {config.min_history}, embargo {config.embargo})"
        )

    records: list[tuple[int, ForecastDistribution]] = []
    refits: list[dict[str, object]] = []
    checks = 0
    for group in _groups(origins, folds, config):
        first = origins[group[0]]
        fit_end = first - config.embargo
        lo = 0 if config.train_window is None else max(0, fit_end + 1 - config.train_window)
        training = bars.iloc[lo : fit_end + 1]
        fit_as_of = available[fit_end]
        assert_known_by(training, fit_as_of, "training history")
        assert_fit_data_precedes(training, available[first], config.embargo, bars)

        guarded = GuardedForecaster(factory(training, fit_as_of))
        refits.append(
            {
                "first_origin": available[first].isoformat(),
                "fit_as_of": fit_as_of.isoformat(),
                "training_bars": len(training),
                "embargo": config.embargo,
            }
        )
        for idx in group:
            i = origins[idx]
            guarded.expected_origin = available[i]
            start = 0 if config.max_history is None else max(0, i + 1 - config.max_history)
            if i + 1 - start < config.min_history:
                raise DataValidationError(
                    f"origin at bar {i} has only {i + 1 - start} bars of context"
                )
            dist = guarded.predict(
                bars.iloc[start : i + 1],
                config.horizon,
                config.n_samples,
                available[i],
                seed=config.seed + idx,
            )
            records.append((i, dist))
        checks += guarded.checks

    notes = []
    if config.step < config.horizon:
        notes.append(
            f"overlapping horizons (stride {config.step} < horizon {config.horizon}): errors are "
            "dependent, so Diebold-Mariano p-values use the overlap-corrected variance"
        )
    scores = scoreset_from_forecasts(
        records[0][1].metadata.model_id,
        records,
        close=close,
        available_ns=available_ns,
        available=available,
        horizon=config.horizon,
        levels=config.levels,
        n_samples=config.n_samples,
        step=config.step,
        extra_notes=tuple(notes),
    )
    return WalkForwardResult(
        scores=scores,
        fold_of_origin=np.asarray(folds, dtype=np.int64),
        refits=tuple(refits),
        guard_checks=checks,
        seconds=time.perf_counter() - started,
    )
