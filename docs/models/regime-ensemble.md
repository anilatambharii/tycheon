# Regime ensemble

Forecaster id `regime-ensemble` · `tycheon.routing.EnsembleForecaster`

Combines several forecasters with the weights of the volatility regime the market is in now.
The random walk is always a member, so the ensemble can never do much worse than "no skill".

## Source

Implemented in Tycheon. A causal regime detector (`VolatilityRegimeDetector`: rolling
volatility clustered by k-means or quantile cuts, re-fitted on a trailing window at each bar;
or `MarkovSwitchingRegimeDetector`: filtered, not smoothed, probabilities from a statsmodels
two-regime model) labels every past forecast origin. `RegimeRouter` scores each member on its
past forecasts with a proper score (the quantile score on a level grid) and turns recent
in-regime relative loss into weights, `exp(-sensitivity * relative loss)`, shrunk toward equal
weights. Regimes with too few origins use the overall weights. `mode="pool"` draws sample
paths from members in proportion to their weights (joint paths kept intact); `mode="quantile"`
averages quantile functions (Vincentisation) for members without paths.

## License

Tycheon: Apache-2.0. statsmodels (BSD-3-Clause) is used by the Markov-switching detector.

## Training data

None of its own. Weights are learned from the members' replayed forecasts at past origins of
the series being forecast, each using only data known at that origin.

## Intended use

Per-asset forecasting where no single model dominates. Its replayed score history is what the
conformal calibrator is fitted on, so the published intervals describe the ensemble, not its
members.

## Limitations

- Weights are only as good as the score history: few origins mean noisy weights, and the
  router says so in its notes instead of pretending.
- Regimes are detected from volatility alone; a regime that does not show up in volatility
  is invisible to it.
- A member the router did not score is refused, not silently ignored.
- `mode="quantile"` has no joint paths, so VaR/ES and drawdown cannot be computed from it.

## Calibration

Uncalibrated as built. Wrap it with `ConformalCalibrator`; the calibrated forecast carries the
holdout coverage that justifies (or withdraws) the `calibrated` label.

## Baseline comparison

The random walk is a member and its weight is reported per regime. When it carries most of
the weight, that is the honest finding. Full evaluation with Diebold-Mariano tests arrives in
Phase T3; not yet measured.

For research and risk analytics. Not investment advice.
