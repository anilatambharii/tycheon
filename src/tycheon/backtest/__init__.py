"""Leakage-proof walk-forward backtesting: engine, guards, metrics and a cost model.

Every input a forecaster sees was published by the origin it is scored at; the data any
router or calibrator is fitted on ends an embargo before the first origin it serves. Models
are always read against the random walk with a Diebold-Mariano test, and results are
published whichever way they fall.
"""

from tycheon.backtest.costs import CostModel, StrategyResult, strategy_result
from tycheon.backtest.factories import (
    calibrated_ensemble_factory,
    calibrated_factory,
    ensemble_factory,
)
from tycheon.backtest.guards import (
    GuardedForecaster,
    assert_fit_data_precedes,
    assert_known_by,
    survivorship_warning,
)
from tycheon.backtest.metrics import (
    DieboldMariano,
    ModelEvaluation,
    diebold_mariano,
    evaluate,
    qlike,
)
from tycheon.backtest.walk_forward import (
    ForecasterFactory,
    WalkForwardConfig,
    WalkForwardResult,
    static,
    walk_forward,
)

__all__ = [
    "CostModel",
    "DieboldMariano",
    "ForecasterFactory",
    "GuardedForecaster",
    "ModelEvaluation",
    "StrategyResult",
    "WalkForwardConfig",
    "WalkForwardResult",
    "assert_fit_data_precedes",
    "assert_known_by",
    "calibrated_ensemble_factory",
    "calibrated_factory",
    "diebold_mariano",
    "ensemble_factory",
    "evaluate",
    "qlike",
    "static",
    "strategy_result",
    "survivorship_warning",
    "walk_forward",
]
