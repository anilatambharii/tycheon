<!-- DRAFT: for maintainer review. Not part of the published site (excluded in mkdocs.yml). -->

# Kronos forecasts the path; Tycheon tells you how much to trust it

*For research and risk analytics. Not investment advice.*

Time-series foundation models can now produce a plausible future price path from a handful of
bars. Kronos, a model pre-trained on candlesticks from many exchanges, is a good example. It
gives you a path. It does not tell you how much to believe it.

That gap is where forecasts get used badly. A point forecast has no stated uncertainty. An
interval a model reports is the model's opinion, not a measured frequency. One model is rarely
best in every market regime. A forecast is not a risk number until someone turns it into one.
And a backtest with any lookahead in it is worse than none, because it is confidently wrong.

Tycheon is an open-source layer that sits on top of Kronos (and TimesFM and Chronos) and tries
to close that gap, in this order: calibrate, add context, route, translate into risk, and
evaluate without leakage.

## What it does

**Calibrated intervals.** Conformal prediction wraps any forecast. The status of a forecast
(`calibrated`, `stale`, `uncalibrated`) is earned on a holdout of forecasts the calibrator had
not seen, and the evidence travels with the forecast.

**Risk, not just paths.** From joint sample paths Tycheon computes VaR and Expected Shortfall,
drawdown probabilities, volatility and stress scenarios, and renders a report. A portfolio of
several assets is always labelled *uncalibrated*, because the dependence between assets is an
assumption, and the report says so.

**An evaluation that can embarrass us.** Every model is scored by a walk-forward engine that
refuses lookahead, and read against the random walk with a Diebold-Mariano test. We publish
the result whichever way it falls.

## What the numbers actually say

We ran the small benchmark: three synthetic series, a 5-bar horizon, 36 origins each, on a
laptop CPU in about eight minutes. Synthetic data is deliberate. The repository ships no market
data, and on a driftless random-walk series no honest model should beat the random walk.

The results were what an honest evaluation of noise should look like:

- **No model beat the random walk.** Kronos-mini, the regime ensemble, a calibrated ensemble,
  GARCH, drift and seasonal naive were all statistically indistinguishable from it.
- **Kronos-mini's raw intervals were overconfident.** Its nominal 90% interval contained 57% of
  outcomes. Its directional accuracy (57%) looked better than a coin flip, but with 108
  forecasts that is well within noise, and the Diebold-Mariano tests agreed.
- **Calibration moved coverage toward nominal.** The calibrated ensemble's 90% interval
  contained 86%, against 80-85% for the uncalibrated baselines, at the cost of a wider
  interval. (That compares an ensemble of baselines, not Kronos itself; calibrating Kronos is
  shown in the tutorial, on one forecast and a thin holdout.)

None of that says Kronos is bad. It says what a calibration-and-evaluation layer is for: on
data with no structure, nothing should look good, and when something does, you want a harness
that makes it prove itself.

## Try it

```bash
git clone https://github.com/anilatambharii/tycheon && cd tycheon
make setup
make benchmark-small        # CPU, a few minutes, renders the leaderboard
uv run python examples/calibrated_kronos.py
uv run python examples/risk_report.py
```

Bring your own data under your own licence. Tycheon never redistributes market data, and a
real, fixed universe gets a survivorship-bias warning automatically.

## What it does not do

It is not a signal service and not investment advice. There is no live brokerage execution, by
design. Calibration is marginal (on average), not a guarantee for the regime you are in now.
The evidence so far is synthetic; the next step is running it on real, point-in-time data with
a licence that allows publishing the results.

*Tycheon is Apache-2.0. The repository, methodology and leaderboard are linked from the
documentation.*
