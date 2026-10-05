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

Uncalibrated: the interval is the one implied by the estimated drift and volatility, not a
measured coverage.

## How Tycheon runs it

Pure NumPy, no downloads, milliseconds per forecast, seeded and reproducible. Requires the
`close` column and at least three bars.

## Baseline comparison

Reported alongside the random walk in every evaluation (Phase T3); not yet measured.

For research and risk analytics. Not investment advice.
