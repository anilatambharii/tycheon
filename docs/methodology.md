# Methodology

This page states what Tycheon intends to do and, just as importantly, what it
refuses to claim. It is written in Phase T0, before the implementation, so that
later phases can be held to it.

## Why this layer exists

A time-series foundation model gives you a trajectory. On its own that is not
usable for risk:

- a point forecast has no stated uncertainty, and a model-reported interval is
  usually not calibrated out of sample;
- OHLCV-only inputs ignore the news, fundamentals and macro context that moves
  prices;
- one model is rarely best across regimes;
- a forecast is not a risk number until it is translated into one;
- and a backtest with any lookahead in it is worse than no backtest, because it
  is confidently wrong.

Tycheon addresses these five in order: calibration, covariates, routing, risk
translation, evaluation.

## Calibration

The intervals Tycheon publishes are **conformal**, built on a held-out
calibration set drawn strictly before the forecast `as_of`. Target coverage is
explicit (for example 90%), and realised coverage is reported alongside it.

What gets published with every forecast:

| Field | Meaning |
|---|---|
| `quantiles` / `interval` | The predictive distribution, not a single number. |
| `calibration_status` | `calibrated`, `stale` or `uncalibrated`, with the window used. |
| `coverage_observed` | Realised coverage on the most recent evaluation window. |
| `model_mix` | Which models contributed, with weights. |
| `as_of` | The timestamp the inputs were restricted to. |
| `model_card` | Reference to the card for every contributing model. |

A forecast whose calibration cannot be established is labelled `uncalibrated`.
It is never silently presented as if it were calibrated.

Financial series break exchangeability: volatility clusters and regimes shift,
so split conformal gives only approximate coverage. Tycheon treats this as a
first-class problem rather than a footnote, and reports reliability diagrams
and coverage-over-time rather than a single headline number.

## Covariates

News, fundamentals and macro series enter as exogenous features, each carrying
its own publication timestamp. The rule is absolute: a feature may only use
information published at or before the forecast `as_of`. Restatements are
handled point-in-time, which means the value a feature carried at that moment,
not the value the vendor shows today.

All external text is **untrusted data**. It is parsed for features; it is never
followed as instruction.

## Routing

Different models win in different regimes. Routing detects regime (volatility
state, trend persistence, liquidity) and weights the ensemble accordingly.
Weights are fit on data before `as_of` only, and the mix is published with the
forecast so a user can see which model spoke.

Baselines are not decoration here: random walk, drift, seasonal naive, ARIMA
and GARCH sit in the same registry as the foundation models and compete on the
same terms.

## Risk translation

From a calibrated predictive distribution Tycheon derives:

- **VaR** at configurable levels,
- **Expected Shortfall** (the average loss beyond VaR),
- **drawdown probability** over a horizon,
- **stress scenarios** (historical and hypothetical shocks),
- **portfolio aggregation** with correlation structure.

Each number inherits the calibration status of the forecast it came from. A VaR
derived from an uncalibrated forecast is labelled as such.

## Evaluation

Walk-forward, with:

- **expanding or rolling windows** and an explicit **embargo** between train
  and test so labels cannot leak backwards;
- **point-in-time inputs** per fold, re-resolved at the fold test-window
  `as_of`;
- **a cost model** (spread, slippage, fees) applied to anything expressed as a
  strategy;
- **the random-walk baseline reported every time**, with a **Diebold-Mariano**
  test on the forecast-error difference and the test statistic published, not
  just a verdict;
- **multiple-comparison discipline**: the more configurations we try, the more
  we must discount the winner.

We publish results when the baseline wins. A leaderboard that only shows wins
is marketing, not evaluation.

## What Tycheon does not claim

- Not that calibrated intervals make a forecast accurate. Calibration is about
  honesty, not skill.
- Not that past coverage guarantees future coverage. Regime change breaks
  coverage, which is why it is monitored rather than assumed.
- Not that any output is a trading recommendation. Tycheon is research and risk
  analytics infrastructure, and **not investment advice**.
