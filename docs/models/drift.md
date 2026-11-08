# Drift

Forecaster id `drift` · `tycheon.models.baselines.DriftForecaster`

A random walk that keeps drifting at its historical average rate.

## Source

Implemented in Tycheon. It is the textbook drift method on log prices: the forecast drift is
the mean historical log return (over `window` returns, default all of them) and the spread
comes from their sample standard deviation. Because the drift is itself an estimate from
`T` returns, each sample path draws its own drift from `N(mean, sigma^2 / T)`. That adds the
standard `h^2 sigma^2 / T` term, giving a forecast variance of `h * sigma^2 * (1 + h / T)`
instead of treating the drift as known. A test checks this variance formula against the
simulated paths.

## License

Apache-2.0, as the rest of Tycheon.

## Training data

None. Fitted from the history passed to `predict` at forecast time.

## Intended use

A second honest comparator. Where a series trends, it separates "the model found structure"
from "the series simply went up".

## Limitations

- Extrapolates a historical mean return that is typically indistinguishable from zero over
  short windows and noisy over long ones; it will happily project a past bull market forward.
- Constant volatility and Gaussian innovations, as in the [random walk](random-walk.md).
- Parameter uncertainty is propagated for the drift only, not for the volatility.

## Calibration

Uncalibrated as built: the interval is the one implied by the estimated drift and volatility. Calibration is available for any forecaster: wrap it with `ConformalCalibrator` (split or
adaptive conformal) and the forecast carries a status (`calibrated`, `stale` or `uncalibrated`)
earned on a holdout it was not fitted on. See [calibration](../calibration.md).

## How Tycheon runs it

Pure NumPy, no downloads, milliseconds per forecast, seeded and reproducible. Requires the
`close` column and at least three bars.

## Baseline comparison

Measured in the small benchmark (`benchmarks/results/small/0.1.0.json`: Tycheon 0.1.0, three synthetic series, horizon 5, 108 pooled walk-forward origins). Pooled MASE against the no-change forecast: 1.024. Achieved coverage of the nominal 90% interval: 79.6%. One-sided Diebold-Mariano p-values that it has lower loss than the random walk: 0.850 (squared error) and 0.905 (CRPS), so it is **indistinguishable from the random walk**. Synthetic data: this shows how the machinery behaves, not whether the model forecasts real markets.

For research and risk analytics. Not investment advice.
