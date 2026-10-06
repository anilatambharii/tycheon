# Changelog

All notable changes to Tycheon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). After 0.1.0 this file is maintained by
[release-please](https://github.com/googleapis/release-please) from Conventional Commits.

For research and risk analytics. Not investment advice.

## [0.1.0] - 2026-10-06

First public release: a calibrated forecasting and risk layer on Kronos, with an evaluation
harness that publishes results whichever way they fall.

### Added

- **Data layer** (`tycheon.data`): bi-temporal point-in-time bars (`timestamp` and
  `available_at`), an append-only DuckDB/Parquet as-of store, pluggable providers (file,
  synthetic sample), and `LookaheadError` for any read of the future.
- **Forecaster contract** (`tycheon.models`): one interface for Kronos (vendored upstream,
  mini/small/base), TimesFM and Chronos-2 adapters (optional extras), and honest baselines
  (random walk, drift, seasonal naive, ARIMA, GARCH). Every forecast carries quantiles,
  optional joint sample paths, calibration status, model mix, `as_of`, a model-card reference
  and the standing disclaimer.
- **Calibration** (`tycheon.calibration`): split and adaptive (ACI) conformal prediction as
  per-level, per-step quantile shifts; a status (`calibrated`, `stale`, `uncalibrated`) earned
  on a holdout; coverage, width, PIT, reliability and CRPS diagnostics; `CalibratedForecaster`.
- **Covariates** (`tycheon.covariates`): bi-temporal covariate store, point-in-time features,
  and a LightGBM residual corrector that is only used if it beats no correction out of sample.
- **Routing** (`tycheon.routing`): causal regime detectors and a regime-weighted ensemble in
  which the random walk is always a candidate.
- **Risk** (`tycheon.risk`): VaR and Expected Shortfall, drawdown probabilities, volatility
  forecasts, stress scenarios, portfolio aggregation with a documented dependence assumption,
  and a JSON and self-contained HTML risk report.
- **Evaluation** (`tycheon.backtest`): rolling-origin walk-forward with refit schedules and an
  embargo, hard leakage guards, a cost and slippage model, a survivorship warning, and MAE,
  RMSE, MASE versus the random walk, directional accuracy, IC and RankIC, CRPS, coverage and
  width, QLIKE and Diebold-Mariano tests.
- **Benchmarks** (`benchmarks/`): a reproducible harness with a dataset manifest, per-model
  configs, versioned JSON results and a generated static leaderboard.
- Documentation site with model cards, ADRs 0001-0006, a benchmark methodology page and
  tutorials.

### Known limitations

- Evidence is synthetic. No claim is made that any model forecasts real markets.
- Multi-asset portfolio risk is always reported `uncalibrated`: dependence is assumed.
- Pooled Diebold-Mariano tests treat series as independent.
- No live brokerage execution, by design. The governed agent layer arrives in a later release.
