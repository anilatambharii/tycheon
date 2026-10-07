# Changelog

All notable changes to Tycheon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). After 0.1.0 this file is maintained by
[release-please](https://github.com/googleapis/release-please) from Conventional Commits.

For research and risk analytics. Not investment advice.

## [0.4.0](https://github.com/anilatambharii/tycheon/compare/v0.3.0...v0.4.0) (2026-10-07)


### Features

* **ee:** migration rollback, queue stats and Prometheus metrics ([7324a06](https://github.com/anilatambharii/tycheon/commit/7324a06be48d52ba7ed65e0e749165f788b9a7f6))
* **helm:** chart for the API, control plane, MCP, CPU/GPU workers, dashboard and OTel collector ([5c9f66c](https://github.com/anilatambharii/tycheon/commit/5c9f66c6bede1580b33b032d38383e34a838e368))


### Bug Fixes

* **helm:** retry the health test and print its logs; ignore three reviewed gitleaks false positives ([d87cfaa](https://github.com/anilatambharii/tycheon/commit/d87cfaa829b499ba46d8b7144b7a60cd504b577d))


### Documentation

* **launch:** README with the benchmark table, examples gallery, blog draft, issue templates, discussion forms, 28 good first issues, Scorecard workflow and the launch checklist ([ac2cf3e](https://github.com/anilatambharii/tycheon/commit/ac2cf3e79a75588645ebe74bd009a71c29fe0166))
* **ops:** SLOs and alerts, runbooks, backup and restore with a tested drill, incident response, CI/CD, SOC 2 aligned controls ([c330638](https://github.com/anilatambharii/tycheon/commit/c330638cb608cf913d9a9ac242085b26dcff52dc))

## [0.3.0](https://github.com/anilatambharii/tycheon/compare/v0.2.0...v0.3.0) (2026-10-07)


### Features

* **ee:** control plane foundation: RLS schema, envelope encryption, plans, metering, rate limits ([59a026d](https://github.com/anilatambharii/tycheon/commit/59a026d87e0f217ff06084c26b6cfcf75055c6b1))
* **ee:** control-plane API: accounts, keys, CSV data sources, credentials, governed metered analytics, MCP ([3717267](https://github.com/anilatambharii/tycheon/commit/3717267378f8f901957e1b4b98fcd03c969a33e5))
* **ee:** OIDC SSO for Enterprise: PKCE, state, nonce, JWKS verification, JIT provisioning ([0d49585](https://github.com/anilatambharii/tycheon/commit/0d49585f334416588595873e0585b9524ae0c129))
* **ee:** per-tenant Kronos fine-tuning with a promotion gate, model registry and routing ([3fabc33](https://github.com/anilatambharii/tycheon/commit/3fabc336060ecb420e8ff7580beed5f057d3eed3))
* **ee:** retention, CLI, SSO browser hand-off, dashboard, acceptance script, CI, docs and ADR 0009 ([77f240b](https://github.com/anilatambharii/tycheon/commit/77f240b25166891b6c8f54b9769cf9ebd538d30c))
* **ee:** Stripe billing in test mode: catalogue from config, checkout, portal, webhooks, usage meters, operator API ([20584f3](https://github.com/anilatambharii/tycheon/commit/20584f3fd12d0142ec8cc95ed757108aab05d7cd))
* **services:** bind per-call data source and private model resolver in the trusted context ([bf53337](https://github.com/anilatambharii/tycheon/commit/bf533376ed453d9acc6f61d2112fa77259dabd5c))


### Bug Fixes

* address CodeQL findings and the gitleaks false positive on PR [#14](https://github.com/anilatambharii/tycheon/issues/14) ([acc31b5](https://github.com/anilatambharii/tycheon/commit/acc31b5cf8529bdfca1ba7d41cbf3250869ddf92))

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
