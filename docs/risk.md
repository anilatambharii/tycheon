# Risk

Risk numbers are computed from joint sample paths of a forecast and carry that forecast's
calibration status. A number is never presented as more trustworthy than the forecast under
it.

## Measures (`tycheon.risk`)

- **VaR and Expected Shortfall** at several levels (default 90, 95, 97.5, 99%) with a
  bootstrap standard error, the number of tail paths, a `reliable` flag (at least 10 tail
  paths) and a `calibrated` flag (true only when the tail lies within the calibrated level
  range).
- **Probability of drawdown beyond X** (max drawdown over the horizon, peak includes the
  start) and of ending the horizon down more than X, each with a Wilson interval.
- **Volatility forecast**: cross-path horizon volatility per bar and annualised, per-path
  realised volatility quantiles, and the model's own conditional sigma when it has one.
- Tested against closed forms: Gaussian and lognormal VaR/ES, and Brownian-motion expected
  drawdown with the discrete-monitoring correction.

## Portfolios and the dependence assumption

`aggregate_portfolio` values a buy-and-hold portfolio along joint paths. Assets are forecast
separately, so their dependence is an **assumption**: a shrunk correlation of daily log
returns estimated from data known at `as_of`, imposed with a Gaussian copula by rank
reordering (Iman-Conover), which leaves each asset's marginal paths untouched.

| Coupling | Keeps | Understates |
|---|---|---|
| `terminal` (default) | each path's own shape and serial dependence | co-movement before the end of the horizon (step-k correlation is about `rho * k / H`), so interim drawdowns |
| `stepwise` | the stated correlation at every step | serial dependence within each path |

A multi-asset portfolio is therefore always `uncalibrated`, with the reason in its notes and
in the report warnings. A single-asset portfolio needs no dependence assumption and keeps
its calibration. Gaussian dependence has no tail dependence: the lower tail in a crash is
likely understated. `stress_correlation` raises correlations to show the sensitivity.

## Stress scenarios

Historical replay of a named window, the worst windows in the history, user shocks, and the
model-implied tail (what the model's own worst 5% of paths look like). None of the
historical or shock scenarios has a probability; the model-implied one is labelled
uncalibrated unless the forecast is.

## The report

`build_risk_report` produces a `RiskReport`: `to_json()` and a self-contained `to_html()`
(inline SVG, no scripts, no external resources, light and dark themes, all dynamic text
escaped). It opens with the warnings that apply to this particular report. Run
`examples/risk_report.py` to produce one for a synthetic three-asset portfolio.

For research and risk analytics. Not investment advice.
