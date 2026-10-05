"""Baselines every evaluation reports against: random walk, drift, seasonal naive, ARIMA, GARCH.

They are first-class forecasters, not decoration. A foundation model that cannot beat
the random walk under a Diebold-Mariano test has not earned a place in the ensemble,
and Tycheon publishes that result whichever way it goes.
"""

from tycheon.models.baselines.arima import ARIMAForecaster
from tycheon.models.baselines.drift import DriftForecaster
from tycheon.models.baselines.garch import GARCHForecaster
from tycheon.models.baselines.random_walk import RandomWalkForecaster
from tycheon.models.baselines.seasonal_naive import SeasonalNaiveForecaster

__all__ = [
    "ARIMAForecaster",
    "DriftForecaster",
    "GARCHForecaster",
    "RandomWalkForecaster",
    "SeasonalNaiveForecaster",
]
