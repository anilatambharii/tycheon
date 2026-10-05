"""Exogenous covariates: news sentiment scores, fundamentals and macro series, as-of stamped.

Every observation carries the time it became known, every feature row uses only what was
known at its own time, and a residual-correction model sits behind an interface so a better
fusion method can replace it.
"""

from tycheon.covariates.features import FeatureBuilder, FeatureSpec, LatestValue, NewsSentiment
from tycheon.covariates.residual import (
    CorrectorReport,
    LightGBMResidualCorrector,
    ResidualCorrectedForecaster,
    ResidualCorrector,
)
from tycheon.covariates.store import CovariateStore

__all__ = [
    "CorrectorReport",
    "CovariateStore",
    "FeatureBuilder",
    "FeatureSpec",
    "LatestValue",
    "LightGBMResidualCorrector",
    "NewsSentiment",
    "ResidualCorrectedForecaster",
    "ResidualCorrector",
]
