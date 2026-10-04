# GARCH(1,1)

Forecaster id `garch` · `tycheon.models.baselines.GARCHForecaster`

The baseline for the thing foundation models are usually not asked about: *how volatile will
it be*.

## Source

Implemented in Tycheon on top of the [arch](https://github.com/bashtage/arch) package
(`arch_model`, version 8.0.0 at the time of writing). A constant-mean GARCH(1,1) with Gaussian
innovations (Bollerslev, 1986) is fitted to the last `window` log returns (default 1000),
scaled to percent, and price paths are simulated from the fitted conditional-variance
recursion, so volatility clustering fattens the horizon-end tails relative to a constant
volatility walk. The per-step conditional standard deviation is returned in
`extras["sigma"]` as a log-return fraction (not percent), shape `(horizon,)`.

## License

Tycheon: Apache-2.0. arch: NCSA, a permissive licence (read from its PyPI metadata).

## Training data

None. Fitted from the history passed to `predict` at forecast time. Needs at least 100 bars.

## Intended use

Volatility forecasting and a risk-aware price comparator. Its `alpha`, `beta` and
`persistence` (their sum) are recorded in the diagnostics, which is itself a quick read on
how clustered a series is.

## Limitations

- Gaussian innovations: tails are thinner than most markets show.
- Symmetric: it does not capture the leverage effect (volatility rising more after falls).
- **Non-stationary fits** (`alpha + beta >= 1`) are possible on short or break-prone
  histories; they are flagged in `diagnostics["non_stationary"]` and forecast volatility then
  does not mean-revert.
- Optimiser warnings are recorded in `diagnostics["fit_warnings"]`, not hidden.
- A constant conditional mean, so no directional view.

## Calibration

Uncalibrated: volatility is the fitted model's own, with parameter uncertainty ignored.

## How Tycheon runs it

CPU only, about 0.02 s per forecast on 800 daily bars (a single observation on a development
laptop). Simulation draws its standard-normal innovations from a seeded generator passed as
`rng`: `arch` reads `random_state` only for its bootstrap method, so seeding that alone would
silently do nothing (a bug the contract tests caught).

## Baseline comparison

Reported alongside the random walk in every evaluation (Phase T3); not yet measured.

For research and risk analytics. Not investment advice.
