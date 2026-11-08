# Random walk

Forecaster id `random-walk` · `tycheon.models.baselines.RandomWalkForecaster`

The baseline every evaluation in Tycheon is read against (AGENTS.md). It is deliberately
simple and deliberately hard to beat honestly: tomorrow looks like today, give or take the
usual noise.

## Source

Implemented in Tycheon. The model is the textbook naive forecast on log prices with a
Gaussian innovation: `log P[t+h] = log P[t] + sum of h iid N(0, sigma^2)`, so the median
forecast is exactly the last close and the spread grows with the square root of the
horizon. `sigma` is the sample standard deviation of the last `window` log returns
(default 250; `window=None` uses all of them), floored at 1e-12 so a flat series cannot
claim zero uncertainty.

## License

Apache-2.0, as the rest of Tycheon.

## Training data

None. It is fitted from the history passed to `predict` at forecast time and sees nothing
else, so it can only see what was available at `as_of`.

## Intended use

The comparator for every other forecaster, and a sanity floor for the pipeline: if a model
cannot beat this under a Diebold-Mariano test, it has not earned a place in the ensemble,
and Tycheon publishes that result whichever way it goes.

## Limitations

- Zero drift: it never expects prices to rise or fall.
- Constant volatility over the horizon: it ignores volatility clustering, so it is too
  narrow after a shock and too wide after a calm spell. The [GARCH](garch.md) baseline
  exists to address exactly that.
- Gaussian innovations: tails are thinner than most markets show.
- Log-normal price distribution: the median equals the last close, the mean slightly
  exceeds it.

## Calibration

Uncalibrated as built: the interval is the Gaussian one implied by the estimated volatility, not a measured coverage. Calibration is available for any forecaster: wrap it with `ConformalCalibrator` (split or
adaptive conformal) and the forecast carries a status (`calibrated`, `stale` or `uncalibrated`)
earned on a holdout it was not fitted on. See [calibration](../calibration.md). Raw coverage of its 90% interval in the small benchmark (`benchmarks/results/small/0.1.0.json`: Tycheon 0.1.0, three synthetic series, horizon 5, 108 pooled walk-forward origins): 79.6%.

## How Tycheon runs it

Pure NumPy, no downloads, milliseconds per forecast. Sample paths are drawn with an explicit
seeded generator (`seed=0` by default; `seed=None` for fresh entropy) and the seed is
recorded in the forecast metadata. Requires the `close` column and at least three bars.

## Baseline comparison

This *is* the baseline. It is mandatory in every benchmark config. In the small benchmark (`benchmarks/results/small/0.1.0.json`: Tycheon 0.1.0, three synthetic series, horizon 5, 108 pooled walk-forward origins) its pooled MASE was 1.014 rather than 1.000. The benchmark scores the median of the sampled paths (100 here), which is close to, not exactly, the last close; that sampling noise adds a little error to a forecast that is otherwise exactly the no-change one. A simulation of the same setup gives noise of the size that accounts for a gap of this order, so it is not evidence of a bug.

For research and risk analytics. Not investment advice.
