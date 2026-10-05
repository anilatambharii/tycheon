"""Tycheon — calibrated financial forecasting and risk.

Kronos forecasts the path; Tycheon tells you how much to trust it.

Tycheon is the forecasting and risk layer: conformal prediction intervals and
reliability diagnostics, exogenous covariates beyond OHLCV, regime-weighted
routing across Kronos / TimesFM / Chronos and honest baselines, translation of
forecasts into VaR, Expected Shortfall and drawdown probabilities, and
leakage-proof walk-forward evaluation.

Three invariants hold everywhere in this package:

* **Point-in-time.** Every data read takes an ``as_of`` and refuses anything
  published after it. Leakage tests are mandatory for every data path.
* **Uncertainty is not optional.** Every forecast carries intervals or
  quantiles, calibration status, the model mix, its ``as_of`` and a model card
  reference.
* **Baselines are published honestly.** Every evaluation reports the
  random-walk baseline and a Diebold-Mariano test, including when the baseline
  wins.

For research and risk analytics. Not investment advice.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tycheon")
except PackageNotFoundError:  # pragma: no cover - only when run from a bare tree
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
