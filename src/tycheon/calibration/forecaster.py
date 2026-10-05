"""A forecaster that calibrates another forecaster's output.

``CalibratedForecaster.fit_on(kronos, bars, ...)`` replays the inner forecaster at past
origins (using only data known at ``as_of``), fits a conformal calibrator on that record, and
from then on returns the inner forecaster's distributions with calibrated quantiles and paths
and the evidence for their status attached.

It satisfies the :class:`~tycheon.models.base.Forecaster` protocol, so it can be routed,
backtested and benchmarked like any model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tycheon.calibration.conformal import AdaptiveConformal, ConformalCalibrator
from tycheon.calibration.scores import collect_scores
from tycheon.data.asof import as_utc

if TYPE_CHECKING:
    from datetime import datetime

    import pandas as pd

    from tycheon.calibration.conformal import Calibrator
    from tycheon.calibration.scores import ScoreSet
    from tycheon.models.base import ForecastDistribution, Forecaster


class CalibratedForecaster:
    """``inner`` with its distributions calibrated by a fitted :class:`ConformalCalibrator`."""

    def __init__(
        self, inner: Forecaster, calibrator: ConformalCalibrator, *, model_id: str | None = None
    ) -> None:
        if calibrator.adjustment is None:
            raise ValueError("fit the calibrator before wrapping a forecaster with it")
        self.inner = inner
        self.calibrator = calibrator
        self.model_id = model_id or inner.model_id
        self.model_card = inner.model_card
        self.supports_paths = inner.supports_paths

    @classmethod
    def from_scores(
        cls,
        inner: Forecaster,
        scores: ScoreSet,
        *,
        method: Calibrator | None = None,
        model_id: str | None = None,
        **calibrator_kwargs: object,
    ) -> CalibratedForecaster:
        """Calibrate ``inner`` from a score history already collected for it."""
        calibrator = ConformalCalibrator(method or AdaptiveConformal(), **calibrator_kwargs)  # type: ignore[arg-type]
        return cls(inner, calibrator.fit(scores), model_id=model_id)

    @classmethod
    def fit_on(
        cls,
        inner: Forecaster,
        bars: pd.DataFrame,
        *,
        as_of: datetime,
        horizon: int,
        n_origins: int = 60,
        n_samples: int = 100,
        max_history: int | None = None,
        method: Calibrator | None = None,
        seed: int = 0,
        model_id: str | None = None,
        **calibrator_kwargs: object,
    ) -> CalibratedForecaster:
        """Replay ``inner`` at ``n_origins`` past origins of ``bars`` and calibrate on the result.

        ``bars`` must be known at ``as_of`` (anything published later raises ``LookaheadError``).
        Use the same ``n_samples`` when forecasting: quantile noise depends on it.
        """
        scores = collect_scores(
            inner,
            bars,
            as_of=as_utc(as_of),
            horizon=horizon,
            n_origins=n_origins,
            n_samples=n_samples,
            max_history=max_history,
            seed=seed,
        )
        return cls.from_scores(inner, scores, method=method, model_id=model_id, **calibrator_kwargs)

    @property
    def report(self):  # type: ignore[no-untyped-def]
        """The calibrator's holdout report (coverage, PIT, reliability, CRPS)."""
        return self.calibrator.report

    def fit(self, history: pd.DataFrame | None = None) -> CalibratedForecaster:  # noqa: ARG002
        """Already fitted at construction; kept for the :class:`Forecaster` protocol."""
        return self

    def predict(
        self,
        history: pd.DataFrame,
        horizon: int,
        n_samples: int,
        as_of: datetime,
        *,
        seed: int | None = None,
    ) -> ForecastDistribution:
        raw = self.inner.predict(history, horizon, n_samples, as_of, seed=seed)
        return self.calibrator.calibrate(raw)
