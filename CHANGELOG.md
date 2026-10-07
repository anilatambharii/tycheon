# Changelog

All notable changes to Tycheon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). After 0.1.0 this file is maintained by
[release-please](https://github.com/googleapis/release-please) from Conventional Commits.

For research and risk analytics. Not investment advice.

## [0.2.0](https://github.com/anilatambharii/tycheon/compare/v0.1.0...v0.2.0) (2026-10-07)


### Features

* **agents:** planner, specialists, independent verifier, composer and workflow ([9d23e9f](https://github.com/anilatambharii/tycheon/commit/9d23e9f62b9893eafd8175f828859e457be16662))
* **governance:** the single Keelgate integration, policy, runtime, loop and example ([0de1c46](https://github.com/anilatambharii/tycheon/commit/0de1c46a20f58018fbdac218724ee30ad2d9a599))
* Phase T4 Keelgate integration, agentic risk review and serving layer ([79f1ebf](https://github.com/anilatambharii/tycheon/commit/79f1ebf38daae4721c4aeda65d07586b1b4c5d94))
* **serve:** bound simultaneous analytics ([bce2716](https://github.com/anilatambharii/tycheon/commit/bce2716e6c511048253875fdb45b9ca4184901c3))
* **serve:** FastAPI REST API with async jobs, API-key auth and OpenAPI; MCP and server CLIs ([50aebde](https://github.com/anilatambharii/tycheon/commit/50aebde092b7acbaa99d1125c8d86831d028b475))
* **services:** typed analytics services and outcome metrics, free of Keelgate ([fcd2124](https://github.com/anilatambharii/tycheon/commit/fcd2124a80317d8cb4e778c4b9c39dbfc315f0e6))


### Bug Fixes

* **governance:** evaluate Rego in a worker process; duckdb and regopy cannot share one on Linux ([dcc2f14](https://github.com/anilatambharii/tycheon/commit/dcc2f142d7ea9ed39e1426486742e2b4ec68a27b))
* **governance:** import duckdb before keelgate; pytest skips Keelgate plugin ([e3058dd](https://github.com/anilatambharii/tycheon/commit/e3058ddbb99c8aee4d597f72f35ecb43446e2728))
* **governance:** refresh grants before they expire; add a governed report-fetch tool ([2add18f](https://github.com/anilatambharii/tycheon/commit/2add18fd7e8e9897c7ee5267ed56380a37123c3b))


### Documentation

* agents, governance and serving pages, ADRs 0007 and 0008, README and safety updates ([0065781](https://github.com/anilatambharii/tycheon/commit/0065781c6510b85fa7a8e19d35ab5882cc74c378))

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
