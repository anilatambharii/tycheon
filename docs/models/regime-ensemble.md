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

The random walk is a member and its weight is reported per regime. When it carries most of the weight, that is the honest finding. Measured in the small benchmark (`benchmarks/results/small/0.1.0.json`: Tycheon 0.1.0, three synthetic series, horizon 5, 108 pooled walk-forward origins). Pooled MASE against the no-change forecast: 1.041. Achieved coverage of the nominal 90% interval: 83.3%. One-sided Diebold-Mariano p-values that it has lower loss than the random walk: 0.991 (squared error) and 0.343 (CRPS), so it is **indistinguishable from the random walk**. The same ensemble calibrated with adaptive conformal prediction (`tycheon-calibrated`) reached 86.1% coverage of the 90% interval, the closest to nominal in that run, with the widest interval; it was also indistinguishable from the random walk (p = 0.787 and 0.797). Synthetic data: this shows how the machinery behaves, not whether the model forecasts real markets.

For research and risk analytics. Not investment advice.
