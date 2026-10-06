# Changelog

All notable changes to Tycheon are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/). After 0.1.0 this file is maintained by
[release-please](https://github.com/googleapis/release-please) from Conventional Commits.

For research and risk analytics. Not investment advice.

## [0.1.1](https://github.com/anilatambharii/tycheon/compare/v0.1.0...v0.1.1) (2026-10-06)


### Features

* add the leaderboard runner and the smoke benchmark config ([ce246ed](https://github.com/anilatambharii/tycheon/commit/ce246ed05050aa9288654d24ad0bb7ba9c68ddd7))
* **backtest:** leakage-guarded walk-forward engine, metrics and cost model ([85c0eaa](https://github.com/anilatambharii/tycheon/commit/85c0eaa800df75b145fdf7469bb5b8ff732f4641))
* **benchmarks:** reproducible harness with manifest, model configs and versioned results ([5c4340e](https://github.com/anilatambharii/tycheon/commit/5c4340e2222ceba40833289548bd375346727411))
* **calibration:** CalibratedForecaster and a shared score-set builder ([807c4d5](https://github.com/anilatambharii/tycheon/commit/807c4d596c04c75137a32c39a37415803ccf3f42))
* **calibration:** split and adaptive conformal with holdout-earned status ([71eadf9](https://github.com/anilatambharii/tycheon/commit/71eadf94d7fdb191ea507e0318d66db2067aef51))
* **covariates:** bi-temporal covariate store and LightGBM residual corrector ([fe6495c](https://github.com/anilatambharii/tycheon/commit/fe6495cad58ed0312e5b0dc8907c6ea5790f3630))
* **data:** point-in-time bars, bi-temporal as-of store and providers; vendor Kronos ([2d543c4](https://github.com/anilatambharii/tycheon/commit/2d543c4e7e56d1a92dbe73e540641707c9d69bf2))
* declare the package layout with T4 governance placeholders ([2122fb8](https://github.com/anilatambharii/tycheon/commit/2122fb897e3366b426a640a02b2d3602238f8e84))
* **models:** carry calibration evidence on ForecastDistribution ([e4dea7f](https://github.com/anilatambharii/tycheon/commit/e4dea7fa8b4a011052ced7a3357751d209f163c8))
* **models:** forecaster contract and the five honest baselines ([eee1031](https://github.com/anilatambharii/tycheon/commit/eee10313cda70a786037e3f5546246feaec7cf68))
* **models:** Kronos, TimesFM and Chronos-2 forecasters ([4080cc5](https://github.com/anilatambharii/tycheon/commit/4080cc5e7452453361f7fadf5166d94d98ddcf90))
* Phase T0 — bootstrap the Tycheon repo ([5bb03cd](https://github.com/anilatambharii/tycheon/commit/5bb03cd59e2df24ebe56ce5919525a6e0cfac012))
* Phase T1 — data layer, forecaster contract, baselines, Kronos/TimesFM/Chronos ([17f35df](https://github.com/anilatambharii/tycheon/commit/17f35df4fdf563a452ceecc4ebf01777eedd9d03))
* Phase T2 calibration, covariates, routing and risk ([a07f499](https://github.com/anilatambharii/tycheon/commit/a07f499650cb154f9851b3e36f9e430b6d1a277e))
* Phase T3 walk-forward evaluation, benchmark harness and release plumbing ([cf6e5e0](https://github.com/anilatambharii/tycheon/commit/cf6e5e03ee28ed6d775cc5cf36634ad9c6a10050))
* **risk:** VaR/ES, drawdown, stress, portfolio aggregation and report ([f1c5327](https://github.com/anilatambharii/tycheon/commit/f1c53271435e14a57fe6beb34b3b11a490d39758))
* **routing:** causal regime detection and regime-weighted ensemble ([4062db7](https://github.com/anilatambharii/tycheon/commit/4062db75337220d60fb783036e2a8eba776db65d))


### Bug Fixes

* **release:** make release-please tag plain vX.Y.Z so the publish workflow triggers ([7f480fc](https://github.com/anilatambharii/tycheon/commit/7f480fc072cfdc34f250d3f63b067b6a61e92571))
* **release:** plain vX.Y.Z tags from release-please ([9a5d8f1](https://github.com/anilatambharii/tycheon/commit/9a5d8f1527c638cfab37b095d6a1ef530716b72f))
* **release:** publish to PyPI without a TestPyPI dry run ([4bd2af4](https://github.com/anilatambharii/tycheon/commit/4bd2af4c71b3f84f2a722107313ab2622daddea8))
* **release:** publish to PyPI without requiring a TestPyPI dry run ([e4cc4ef](https://github.com/anilatambharii/tycheon/commit/e4cc4ef61023abe78f3f3f8cfba0ce40f4e26131))
* **types:** satisfy mypy --strict under the numpy stubs resolved on Python 3.12 ([de3f35e](https://github.com/anilatambharii/tycheon/commit/de3f35e852c4017339e731ee50a247f380d82d4b))


### Documentation

* add README, contributor policies and the mkdocs skeleton ([669b6c2](https://github.com/anilatambharii/tycheon/commit/669b6c25bb619c7dc192a4a61317b4d286c77c4e))
* benchmark methodology, ADR 0006, tutorials and calibrated Kronos example ([ad6129a](https://github.com/anilatambharii/tycheon/commit/ad6129a8340874fbe96246ef334858efaadf8cd3))
* **benchmarks:** publish the small benchmark results and leaderboard ([df1aedd](https://github.com/anilatambharii/tycheon/commit/df1aeddeaa85c85d63daca4751b519fd8a9f82f0))
* calibration, risk, model cards, ADRs 0004-0005, risk_report example ([c1ddc13](https://github.com/anilatambharii/tycheon/commit/c1ddc130aeb4d096c1f3b308478658c18a1a4fcb))
* model cards, example, README and benchmark alignment ([2068cce](https://github.com/anilatambharii/tycheon/commit/2068ccee4918ce159351923eaf8dad761a9e02f8))
* set the open-core licence boundary with ADR 0001 ([5aed393](https://github.com/anilatambharii/tycheon/commit/5aed39320b1659acfe666a56eab04a6168b33188))

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
