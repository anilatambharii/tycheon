"""Factories that build Tycheon's own forecasters from training history, for walk-forward runs.

A factory receives only the training history (which the engine has already checked ends
before the embargo) and the moment it was known, so the router and calibrator it fits can
use nothing later.

* :func:`ensemble_factory`: score the member forecasters at past origins of the training
  history, learn per-regime weights (random walk always a member), return an
  :class:`~tycheon.routing.EnsembleForecaster`.
* :func:`calibrated_factory`: wrap any factory's forecaster in conformal calibration fitted on
  that forecaster's own replayed record.
* :func:`calibrated_ensemble_factory`: the ensemble calibrated on its *own online replay*, so
  the calibration record is honest about weights that were chosen with past data only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tycheon.calibration.conformal import AdaptiveConformal
from tycheon.calibration.forecaster import CalibratedForecaster
from tycheon.calibration.scores import collect_scores
from tycheon.routing import (
    RANDOM_WALK_ID,
    EnsembleForecaster,
    RegimeRouter,
    VolatilityRegimeDetector,
    labels_for_scores,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import pandas as pd

    from tycheon.backtest.walk_forward import ForecasterFactory
    from tycheon.calibration.conformal import Calibrator
    from tycheon.models.base import Forecaster


def _build_ensemble(
    history: pd.DataFrame,
    as_of: pd.Timestamp,
    members: Mapping[str, Callable[[], Forecaster]],
    *,
    horizon: int,
    n_origins: int,
    n_samples: int,
    max_history: int | None,
    mode: str,
    seed: int,
) -> tuple[EnsembleForecaster, RegimeRouter, dict, object]:  # type: ignore[type-arg]
    if RANDOM_WALK_ID not in members:
        raise ValueError(f"{RANDOM_WALK_ID} must always be an ensemble member")
    built = {name: make() for name, make in members.items()}
    scores = {
        name: collect_scores(
            model,
            history,
            as_of=as_of,
            horizon=horizon,
            n_origins=n_origins,
            n_samples=n_samples,
            max_history=max_history,
            seed=seed,
        )
        for name, model in built.items()
    }
    detector = VolatilityRegimeDetector(n_regimes=2, window=20, lookback=500, min_history=150)
    labels = labels_for_scores(detector, history, next(iter(scores.values())))
    router = RegimeRouter()
    weights = router.fit(scores, labels)
    ensemble = EnsembleForecaster(built, detector, weights, mode=mode, seed=seed)
    return ensemble, router, scores, labels


def ensemble_factory(
    members: Mapping[str, Callable[[], Forecaster]],
    *,
    horizon: int,
    n_origins: int = 60,
    n_samples: int = 60,
    max_history: int | None = 500,
    mode: str = "pool",
    seed: int = 0,
) -> ForecasterFactory:
    """Factory for the regime ensemble over ``members`` (name -> zero-argument constructor)."""

    def make(history: pd.DataFrame, as_of: pd.Timestamp) -> Forecaster:
        ensemble, _, _, _ = _build_ensemble(
            history, as_of, members, horizon=horizon, n_origins=n_origins,
            n_samples=n_samples, max_history=max_history, mode=mode, seed=seed,
        )  # fmt: skip
        return ensemble

    return make


def calibrated_factory(
    base: ForecasterFactory,
    *,
    horizon: int,
    n_origins: int = 60,
    n_samples: int = 100,
    max_history: int | None = 500,
    method: Callable[[], Calibrator] = AdaptiveConformal,
    model_id: str | None = None,
    seed: int = 0,
) -> ForecasterFactory:
    """Factory that calibrates whatever ``base`` builds, on that forecaster's own replay."""

    def make(history: pd.DataFrame, as_of: pd.Timestamp) -> Forecaster:
        return CalibratedForecaster.fit_on(
            base(history, as_of), history, as_of=as_of, horizon=horizon, n_origins=n_origins,
            n_samples=n_samples, max_history=max_history, method=method(), seed=seed,
            model_id=model_id,
        )  # fmt: skip

    return make


def calibrated_ensemble_factory(
    members: Mapping[str, Callable[[], Forecaster]],
    *,
    horizon: int,
    n_origins: int = 60,
    n_samples: int = 100,
    max_history: int | None = 500,
    method: Callable[[], Calibrator] = AdaptiveConformal,
    model_id: str | None = None,
    seed: int = 0,
) -> ForecasterFactory:
    """The ensemble calibrated on its own online replay (weights chosen with past data only)."""

    def make(history: pd.DataFrame, as_of: pd.Timestamp) -> Forecaster:
        ensemble, router, scores, labels = _build_ensemble(
            history, as_of, members, horizon=horizon, n_origins=n_origins,
            n_samples=n_samples, max_history=max_history, mode="pool", seed=seed,
        )  # fmt: skip
        replay = router.replay(scores, labels, seed=seed)  # type: ignore[arg-type]
        return CalibratedForecaster.from_scores(
            ensemble, replay, method=method(), model_id=model_id
        )

    return make
