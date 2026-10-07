# Tycheon

**Kronos forecasts the path; Tycheon tells you how much to trust it.**

Tycheon is the open-source calibrated forecasting and risk layer for financial
time series. Foundation models for time series will happily hand you a
trajectory with no honest statement of its uncertainty, no check that its
intervals hold up out of sample, and no translation into the numbers a risk
desk actually uses. Tycheon is that missing layer.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](pyproject.toml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/anilatambharii/tycheon/badge)](https://scorecard.dev/viewer/?uri=github.com/anilatambharii/tycheon)

**For research and risk analytics. Not investment advice.**

> **Status: v0.3.0, alpha.** The library, a governed agentic risk review, a REST API and an
> MCP server exist and are tested. **The evidence so far is synthetic: on the bundled series
> no model, Kronos included, is distinguishable from the random walk, and we publish that**
> (see [Benchmark results](#benchmark-results) below). Multi-asset portfolio risk is always
> labelled **uncalibrated**: dependence is assumed. See [What works, what is experimental,
> what is not verified](#what-works-what-is-experimental-what-is-not-verified).

![Kronos-small beside the random-walk baseline on a synthetic series, from examples/forecast.py](docs/assets/forecast-example.png)

*Kronos-small and the random walk, forecasting the same synthetic series from the same
history (`make example`). On this one series Kronos-small's interval is about three times
narrower and the realised path leaves it: a single anecdote, not a result, and exactly
the over-confidence the calibration phases exist to measure.*

## What it does

| Capability | What that means |
|---|---|
| **Calibrated uncertainty** | Conformal prediction intervals with reliability diagnostics, so 90% coverage means 90% coverage. |
| **Exogenous covariates** | News, fundamentals and macro features — not OHLCV alone. |
| **Multi-model routing** | Kronos, TimesFM, Chronos and honest baselines, weighted by detected regime. |
| **Forecast-to-risk** | VaR, Expected Shortfall, drawdown probability, stress scenarios, portfolio aggregation. |
| **Leakage-proof evaluation** | Walk-forward backtesting with embargoes, a cost model, and a public leaderboard. |
| **Production serving** | REST and MCP, plus a governed agentic research workflow. |

Tycheon is B2B risk-analytics infrastructure for developers and fintechs. It is
**not** a consumer trade-signal product and it does **not** give personalized
investment advice.

## Architecture

```mermaid
flowchart TB
    subgraph sources["Data sources (you bring the license)"]
        md["Market data provider"]
        news["News / filings"]
        fund["Fundamentals / macro"]
    end

    subgraph data["tycheon.data — point-in-time store"]
        asof["as_of gate<br/>refuses anything published after as_of"]
    end

    subgraph models["tycheon.models"]
        kronos["Kronos"]
        tfm["TimesFM"]
        chron["Chronos"]
        base["Baselines<br/>RW · drift · seasonal naive · ARIMA · GARCH"]
    end

    cov["tycheon.covariates<br/>news · fundamentals · macro"]
    route["tycheon.routing<br/>regime detection → ensemble weights"]
    cal["tycheon.calibration<br/>conformal intervals + reliability"]
    risk["tycheon.risk<br/>VaR · ES · drawdown prob · stress"]
    bt["tycheon.backtest<br/>walk-forward · embargo · costs · leakage guards"]
    serve["tycheon.serve<br/>REST + MCP"]

    gov["tycheon.governance<br/>the only place Keelgate is imported"]
    agents["tycheon.agents<br/>planner · specialists · verifier"]

    md --> asof
    news --> asof
    fund --> asof
    asof --> models
    asof --> cov
    cov --> route
    models --> route
    route --> cal
    cal --> risk
    cal --> bt
    risk --> bt
    risk --> serve
    cal --> serve
    bt -.->|publishes| lb["benchmarks/<br/>public leaderboard"]
    agents --> gov
    gov -->|governed tools| serve

    classDef future stroke-dasharray: 5 5
    class gov,agents future
```

Every forecast that leaves this system carries its intervals, its calibration
status, the model mix that produced it, its `as_of`, and a model card
reference. Every evaluation reports the random-walk baseline and a
Diebold-Mariano test — including when the baseline wins.

## Install

```bash
pip install tycheon                 # core: baselines, calibration, risk, backtest (no torch)
pip install "tycheon[report]"       # + the HTML risk report
```

Foundation models are optional extras, so the base install stays small
(`import tycheon.models` never imports torch): `tycheon[kronos]`, `[timesfm]`, `[chronos]`.
The `serve` and `agents` extras depend on [Keelgate](https://github.com/anilatambharii/keelgate),
which this repo pins to a commit through its `uv` sources; to use the REST API, the MCP server or
the agents, work from a clone (below), which is the path we test. PyPI lists `tycheon` 0.3.0 at the
time of writing.

## Quickstart

This runs on the base install, offline, on bundled **synthetic** data (Tycheon ships no market
data). A forecast is a distribution, always, and every read is point-in-time:

```python
from tycheon.data import load_sample
from tycheon.errors import LookaheadError
from tycheon.models.baselines import GARCHForecaster, RandomWalkForecaster

bars = load_sample("SYN-GARCH")  # synthetic series with two clocks: timestamp, available_at
history = bars.iloc[:900]
as_of = history["available_at"].iloc[-1]  # when this history became known

for model in (RandomWalkForecaster(), GARCHForecaster()):
    forecast = model.predict(history, horizon=10, n_samples=200, as_of=as_of)
    print(forecast.summary())  # quantiles, calibration status, model mix, as_of, disclaimer

try:  # bars published after as_of are refused before any model runs
    RandomWalkForecaster().predict(bars, horizon=10, n_samples=50, as_of=as_of)
except LookaheadError as exc:
    print("refused:", exc)
```

Output, from `uv run python` on a clone of this repository (not separately re-run against the PyPI wheel):

```text
random-walk [uncalibrated, sample paths] as_of 2021-06-12T00:00:00+00:00: last close 95.32; step 10 median 95.19, 90% interval [89.86, 99.72]. For research and risk analytics. Not investment advice.
garch [uncalibrated, sample paths] as_of 2021-06-12T00:00:00+00:00: last close 95.32; step 10 median 95.14, 90% interval [88.75, 100.5]. For research and risk analytics. Not investment advice.
refused: available_at reaches 2023-09-30T00:00:00+00:00, after as_of=2021-06-12T00:00:00+00:00
```

Note that both forecasts say `uncalibrated`: calibration is something you earn on a holdout
(see [calibration](docs/calibration.md)), not a label. Kronos works the same way
(`KronosForecaster("small")`, needs `uv sync --extra kronos`), see
[`examples/forecast.py`](examples/forecast.py). More runnable material is in the
[examples gallery](docs/gallery.md): MCP in Claude Desktop and Cursor, a portfolio risk report,
and fine-tuning through Tycheon Cloud.

To develop (requires [uv](https://docs.astral.sh/uv/) and Python 3.11+):

```bash
git clone https://github.com/anilatambharii/tycheon.git
cd tycheon
make setup          # venv + dev deps + CPU torch (for Kronos) + git hooks
make check          # lint, format check, mypy --strict, fast tests
make example        # Kronos-small beside the random walk -> examples/output/forecast.png
```

Optional dev services (Postgres, Redis, MinIO, Jaeger):

```bash
cp .env.example .env
make up             # start and wait for health
make down           # stop and delete volumes
```

Extras for working from a clone:

```bash
uv sync --extra kronos      # Kronos (vendored) + torch
uv sync --extra timesfm     # TimesFM 2.5
uv sync --extra chronos     # Chronos-2
uv sync --extra serve       # FastAPI + MCP server
uv sync --all-extras        # everything
```

## Benchmark results

**On the only data the public benchmark uses so far, nothing beats the random walk.** The
numbers below are copied from
[`benchmarks/results/small/0.1.0.json`](benchmarks/results/small/0.1.0.json) (benchmark `small`,
run 2026-10-05T15:41:15+00:00 on CPU, Tycheon 0.1.0, 743.6 s, git commit `5c344b4660` with
uncommitted changes, as the file itself records). It is a smoke-sized run: 3 **synthetic** series
(GBM, GARCH, two-regime volatility), horizon 5 bars, 36 non-overlapping forecast origins per series,
108 pooled origins. Every model is scored by the same leakage-guarded walk-forward engine at the same
origins.

| Model | MASE vs RW | RMSE (%) | CRPS (%) | 90% interval coverage | DM p, sq. error | DM p, CRPS | Diebold-Mariano outcome |
|---|--:|--:|--:|--:|--:|--:|---|
| **random-walk** (baseline) | 1.014 | 3.229 | 1.752 | 79.6% | n/a | n/a | the reference |
| drift (baseline) | 1.024 | 3.264 | 1.778 | 79.6% | 0.850 | 0.905 | indistinguishable from the random walk |
| seasonal-naive (baseline) | 1.012 | 3.244 | 1.738 | 85.2% | 0.618 | 0.269 | indistinguishable from the random walk |
| garch (baseline) | 1.028 | 3.267 | 1.734 | 83.3% | 0.929 | 0.258 | indistinguishable from the random walk |
| kronos-mini (zero-shot) | 1.025 | 3.306 | 1.819 | 57.4% | 0.685 | 0.756 | indistinguishable from the random walk |
| tycheon-ensemble | 1.041 | 3.298 | 1.744 | 83.3% | 0.991 | 0.343 | indistinguishable from the random walk |
| tycheon-calibrated | 1.039 | 3.281 | 1.781 | 86.1% | 0.787 | 0.797 | indistinguishable from the random walk |

How to read it. MASE is the model's MAE over the random walk's MAE on the same origins (below 1
beats it). "DM p" is the one-sided Diebold-Mariano p-value, with the Harvey-Leybourne-Newbold
correction, that the model has lower loss than the random walk; a model counts as beating the
random walk only if **both** tests give p < 0.05. Nominal coverage is 90%.

What this says, plainly:

- **None of the 6 models other than the random-walk reference beats it; all 6 are statistically
  indistinguishable from it.** The best RMSE is the random walk itself, the best MASE is
  seasonal-naive and the best CRPS is GARCH: a baseline leads every headline column, and the
  differences are not statistically distinguishable at this sample size.
- **Zero-shot Kronos-mini's 90% interval contained 57.4% of outcomes**, against 79.6% to 86.1% for
  the others: its raw intervals are overconfident. Its 57.4% directional accuracy and IC of 0.168
  are not distinguishable from chance with 108 origins, and the DM tests agree.
- **The calibrated ensemble's 90% interval contained 86.1%** (closest to nominal in the table), at
  the cost of the widest interval. That ensemble is built from baselines, not from Kronos, so this
  is not evidence that calibrating Kronos works.
- TimesFM and Chronos are not in this run.

This is a machinery check on known processes, not evidence about markets: synthetic data, small
samples, no live trading, no regime coverage beyond the generated series. Read the
[full leaderboard](docs/leaderboard/index.md) (per-series results, interval coverage at 50/80/90%,
a cost-aware diagnostic) and the [benchmark methodology](docs/benchmark-methodology.md) for the
leakage controls and the limits. Reproduce with
`python -m benchmarks.run --config benchmarks/configs/small.yaml --execute` (`make benchmark-small`);
the same code rendered the leaderboard page from the same JSON file.

## What works, what is experimental, what is not verified

| | |
|---|---|
| **Works (tested in CI)** | Point-in-time data layer with `as_of` enforcement and leakage tests; the baselines (random walk, drift, seasonal naive, ARIMA, GARCH); conformal calibration with a holdout-based status; VaR / ES / drawdown / stress and the HTML risk report; the walk-forward engine with guards, embargo and Diebold-Mariano tests; REST API and MCP server (stdio) over governed tools; an offline scripted agent review with an independent verifier. |
| **Experimental** | The regime-weighted ensemble and calibrated forecaster (evaluated only on synthetic data so far); Kronos adapters and zero-shot use (the vendored model runs, but no evidence yet that it adds value on market data); TimesFM and Chronos adapters (quantiles only; not in the published benchmark run); the covariate residual corrector; the agent workflow beyond the scripted model; Tycheon Cloud (`ee/`, proprietary). |
| **Not verified** | Any forecasting skill on real market data. Any trading or investment outcome (there is no live execution, by design). Tycheon Cloud against live Stripe, AWS KMS, GPUs or a browser-driven dashboard (see [cloud](docs/cloud.md)). Multi-asset dependence (always reported `uncalibrated`). The `examples/cloud_finetune.py` script against a live backend. |

## Documentation

[Methodology](docs/methodology.md) | [Calibration](docs/calibration.md) | [Risk](docs/risk.md) |
[Benchmark methodology](docs/benchmark-methodology.md) | [Leaderboard](docs/leaderboard/index.md) |
[Serving (REST and MCP)](docs/serving.md) | [Agents](docs/agents.md) | [Governance](docs/governance.md) |
[Examples gallery](docs/gallery.md) | [Model cards](docs/models/index.md) |
[Tycheon Cloud](docs/cloud.md) | [Safety and compliance](docs/safety.md) |
[ADRs](docs/adr/0001-licensing-and-open-core.md)

## Governed agents and serving

A model can plan and draft; it cannot call a tool, pick the date, or approve anything. Specialist
agents gather evidence through **governed tool calls** (a grant per agent, a deterministic policy,
a tamper-evident audit chain); an **independent verifier** checks every number in the draft against
the evidence it cites; a report no draft can verify is **withheld**; a paper trade is only ever a
**proposal** that waits for a human.

```bash
uv sync --extra agents --extra report
uv run python examples/agentic_risk_review.py     # offline, scripted model: shows a rejection,
                                                  # a revision, and a human approval request
tycheon-serve --insecure-dev                      # REST + OpenAPI at http://127.0.0.1:8080/docs
tycheon-mcp                                       # the same tools over MCP (stdio)
make evals                                        # trajectory, planted-error and injection evals
```

See [agents](docs/agents.md), [governance](docs/governance.md) and [serving](docs/serving.md).
The evidence is still synthetic, and Keelgate's own eval and telemetry modules are not built yet
(the governance page lists exactly what was substituted).

## Models

Every forecaster returns a `ForecastDistribution` and ships a
[model card](docs/models/index.md).

| Forecaster | What it is | Sample paths | Card |
|---|---|---|---|
| `kronos-mini` / `-small` / `-base` | Zero-shot Kronos, 2048 / 512 / 512-bar context | yes | [mini](docs/models/kronos-mini.md), [small](docs/models/kronos-small.md), [base](docs/models/kronos-base.md) |
| `timesfm-2.5-200m` | Zero-shot TimesFM 2.5 | quantiles only | [card](docs/models/timesfm.md) |
| `chronos-2` | Zero-shot Chronos-2 | quantiles only | [card](docs/models/chronos-2.md) |
| `random-walk` | Driftless log-price walk: the baseline everything is read against | yes | [card](docs/models/random-walk.md) |
| `drift`, `seasonal-naive`, `arima`, `garch` | The other honest baselines | yes | [drift](docs/models/drift.md), [seasonal-naive](docs/models/seasonal-naive.md), [arima](docs/models/arima.md), [garch](docs/models/garch.md) |

Kronos is vendored from upstream at a pinned commit ([ADR 0002](docs/adr/0002-kronos-integration.md)),
with its sampler replaced so the sample paths are kept rather than averaged away.

Run the small benchmark (CPU, synthetic data, a few minutes) and render the leaderboard:

```bash
make benchmark-small
```

Tutorials: [calibrated forecasts with Kronos](docs/tutorials/calibrated-kronos.md) and
[run the benchmark](docs/tutorials/run-the-benchmark.md). How the leaderboard stays honest:
[benchmark methodology](docs/benchmark-methodology.md).

> **No `make` on Windows?** Every target is a one-line `uv` command; open the
> [`Makefile`](Makefile) and run them directly, e.g. `uv sync` then
> `uv run ruff check . && uv run mypy && uv run pytest -m "not slow"`.

## Project layout

```
src/tycheon/
  data/          provider protocol, as-of store, synthetic sample data
  models/        kronos, timesfm, chronos, baselines
  calibration/   conformal intervals, diagnostics
  covariates/    news, fundamentals, macro features
  routing/       regime detection, ensemble weighting
  risk/          VaR, ES, drawdown prob, stress, portfolio aggregation
  backtest/      walk-forward, metrics, cost model, leakage guards
  services/      typed analytics (forecast, calibrate, risk, backtest, news, fundamentals)
  governance/    the only place Keelgate is imported: grants, policy, approvals, audit, loop
  agents/        planner, specialists, independent verifier, composer (pure Python)
  serve/         FastAPI REST API and MCP server (over the same governed tools)
benchmarks/      leaderboard harness, configs, results
third_party/     vendored upstream Kronos (MIT), byte-identical, hash-checked
docs/            methodology, ADRs, model cards
ee/              proprietary Tycheon Cloud — separate license
```

## Safety and data rules

These are not aspirations; they are enforced in tests and CI.

- **Point-in-time everything.** Every data read takes an `as_of` and refuses
  data published after it. Leakage tests are mandatory for every data path.
- **No live execution.** v1 is paper and simulation only, and only through
  Keelgate-governed tools.
- **Uncertainty is not optional.** No forecast ships without intervals,
  calibration status, model mix, `as_of` and a model card.
- **Honest baselines.** Random walk plus Diebold-Mariano on every evaluation,
  published whichever way it goes.
- **External text is data, never instructions.** News and filings are untrusted
  input; nothing in them is ever executed.
- **Your data licence stays yours.** Providers are pluggable, customers bring
  their own market-data licence, and Tycheon never redistributes licensed
  exchange data. `yfinance` appears in examples and local dev only, clearly
  labelled, never in the cloud product.

More in [`docs/safety.md`](docs/safety.md) and [`SECURITY.md`](SECURITY.md).

## Sister project

[Keelgate](https://github.com/anilatambharii/keelgate) is the safety harness —
policy gates, capabilities, approvals, audit, durable loops, telemetry and
evals. Tycheon depends on it as a library (the `agents` and `serve` extras), and every
Keelgate import is confined to
[`src/tycheon/governance/`](src/tycheon/governance/README.md). Keelgate is not on
PyPI yet, so those extras are installed from a pinned commit for now; see
[governance](docs/governance.md).

## Open core

Everything outside `ee/` is Apache-2.0. `ee/` is Tycheon Cloud and carries its
own proprietary licence. The boundary and the rules for it are in
[`docs/adr/0001-licensing-and-open-core.md`](docs/adr/0001-licensing-and-open-core.md).

Kronos is used under its upstream MIT licence
([shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos)). Its code is vendored in
[`third_party/kronos/`](third_party/kronos/README.md) with the original licence file,
which also ships inside the wheel.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). `make check` must pass, tests ship
with code, and no PR lands without them. Report vulnerabilities privately, per
[`SECURITY.md`](SECURITY.md); lookahead leakage counts as one.

---

**For research and risk analytics. Not investment advice.** Nothing produced by
this software is a recommendation to buy or sell any security, and no part of it
is personalized financial advice. Past calibration does not guarantee future
coverage.
