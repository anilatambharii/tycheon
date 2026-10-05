"""Kronos adapter. Needs the ``kronos`` extra for the model itself.

Importing this package is cheap and does not need torch: the forecaster, the sampler
and the vendored upstream all import torch lazily, when a forecast is requested.
"""

from tycheon.models.kronos.forecaster import KronosForecaster
from tycheon.models.kronos.specs import KRONOS_SPECS, KronosSpec

__all__ = ["KRONOS_SPECS", "KronosForecaster", "KronosSpec"]
