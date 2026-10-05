"""Conformal calibration and diagnostics: how much to trust a forecast.

The workflow is: replay a forecaster at past origins to get a :class:`ScoreSet`
(:func:`collect_scores`), fit a :class:`ConformalCalibrator` on it, and calibrate new
forecasts. Every calibrated forecast carries its evidence (:class:`CalibrationInfo`), and
calibration data published after a forecast's ``as_of`` is refused.
"""

from tycheon.calibration.conformal import (
    AdaptiveConformal,
    ConformalCalibrator,
    QuantileAdjustment,
    SplitConformal,
    calibrate,
)
from tycheon.calibration.diagnostics import CalibrationReport, CoverageResult, evaluate_calibration
from tycheon.calibration.forecaster import CalibratedForecaster
from tycheon.calibration.scores import DEFAULT_LEVELS, ScoreSet, collect_scores

__all__ = [
    "DEFAULT_LEVELS",
    "AdaptiveConformal",
    "CalibratedForecaster",
    "CalibrationReport",
    "ConformalCalibrator",
    "CoverageResult",
    "QuantileAdjustment",
    "ScoreSet",
    "SplitConformal",
    "calibrate",
    "collect_scores",
    "evaluate_calibration",
]
