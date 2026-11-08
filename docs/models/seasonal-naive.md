# Seasonal naive

Forecaster id `seasonal-naive` · `tycheon.models.baselines.SeasonalNaiveForecaster`

"The next `m` bars look like the last `m` bars."

## Source

Implemented in Tycheon. It simulates a seasonal random walk on log prices,
`y[t] = y[t - m] + e[t]`, where `e` is Gaussian with a standard deviation estimated from the
history's own seasonal differences `y[t] - y[t - m]`. Paths are generated recursively, so
steps `m` apart share their innovations and the sample paths are properly joint rather than
independent draws; a test verifies the correlation structure. The season length `m` is a
constructor argument (default 5, a trading week of daily bars; use 24 for hourly bars).

## License

Apache-2.0, as the rest of Tycheon.

## Training data

None. Fitted from the history passed to `predict` at forecast time. Needs at least
`m + 3` bars.

## Intended use

The standard comparator for series with a repeating pattern, and a useful failure: on a
series without seasonality its error is large, which the leaderboard should show.

## Limitations

- Applied to price *levels*, which have no stable seasonal pattern, it is a weak baseline by
  design: its median forecast is the price `m` bars ago, not the latest price.
- The season length is not inferred; a wrong `m` is a wrong model.
- Constant-variance Gaussian innovations.

## Calibration

Uncalibrated as built: the spread is the Gaussian one implied by the seasonal differences. Calibration is available for any forecaster: wrap it with `ConformalCalibrator` (split or
adaptive conformal) and the forecast carries a status (`calibrated`, `stale` or `uncalibrated`)
earned on a holdout it was not fitted on. See [calibration](../calibration.md).

## How Tycheon runs it

Pure NumPy, no downloads, milliseconds per forecast, seeded and reproducible.

## Baseline comparison

Measured in the small benchmark (`benchmarks/results/small/0.1.0.json`: Tycheon 0.1.0, three synthetic series, horizon 5, 108 pooled walk-forward origins). Pooled MASE against the no-change forecast: 1.012. Achieved coverage of the nominal 90% interval: 85.2%. One-sided Diebold-Mariano p-values that it has lower loss than the random walk: 0.618 (squared error) and 0.269 (CRPS), so it is **indistinguishable from the random walk**. It had the lowest pooled MASE in that run, a difference that is not statistically distinguishable. Synthetic data: this shows how the machinery behaves, not whether the model forecasts real markets.

For research and risk analytics. Not investment advice.
