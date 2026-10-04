"""Chronos adapter. Needs the ``chronos`` extra; the import itself needs nothing."""

from tycheon.models.chronos.forecaster import ChronosForecaster

__all__ = ["ChronosForecaster"]
