# ARIMA

Forecaster id `arima` · `tycheon.models.baselines.ARIMAForecaster`

## Source

Implemented in Tycheon on top of [statsmodels](https://www.statsmodels.org)
(`statsmodels.tsa.arima.model.ARIMA`, version 0.15.0 at the time of writing). An ARIMA(p, d, q)
with a constant is fitted to the last `window` log returns (default order `(1, 0, 1)`,
default window 500), scaled to percent so the optimiser is well conditioned, and simulated
forward with `simulate(..., anchor="end")`. Prices are rebuilt from the simulated returns, so
paths stay positive and carry the fitted model's serial dependence and innovation variance.

## License

Tycheon: Apache-2.0. statsmodels: BSD-3-Clause (read from its PyPI metadata).

## Training data

None. Fitted from the history passed to `predict` at forecast time. Needs at least 30 bars.

## Intended use

A classical linear-time-series comparator. On daily returns the fitted AR and MA terms are
usually close to zero, so ARIMA tends to behave like a random walk with a small drift, which
is itself informative.

## Limitations

- **Parameter uncertainty is not propagated.** Paths are conditional on the point estimates,
  so intervals are optimistic for short histories.
- Linear and homoskedastic: no volatility clustering (see [GARCH](garch.md)).
- Fit warnings (non-convergence, boundary estimates) are **recorded in
  `metadata.diagnostics["fit_warnings"]`**, not suppressed; a forecast carrying them should
  be read with that in mind.
- The order is fixed by the caller, not selected.

## Calibration

Uncalibrated: the spread is the fitted model's own.

## How Tycheon runs it

CPU only, roughly 0.2 s per fit-and-simulate on 800 daily bars (a single observation on a
development laptop). Seeded and reproducible through an explicit generator.

## Baseline comparison

Reported alongside the random walk in every evaluation (Phase T3); not yet measured.

For research and risk analytics. Not investment advice.
