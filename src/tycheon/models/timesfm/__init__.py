"""TimesFM adapter. Needs the ``timesfm`` extra; the import itself needs nothing."""

from tycheon.models.timesfm.forecaster import TimesFMForecaster

__all__ = ["TimesFMForecaster"]
