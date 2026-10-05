"""Forecasting models: foundation models and honest baselines behind one interface.

Importing this package never imports torch, TimesFM or Chronos; those load lazily,
inside the forecaster that needs them, so the base install stays light.
"""

from tycheon.models.base import (
    DEFAULT_QUANTILE_LEVELS,
    DISCLAIMER,
    BaseForecaster,
    ForecastDistribution,
    Forecaster,
    ForecastMetadata,
)

__all__ = [
    "DEFAULT_QUANTILE_LEVELS",
    "DISCLAIMER",
    "BaseForecaster",
    "ForecastDistribution",
    "ForecastMetadata",
    "Forecaster",
]
