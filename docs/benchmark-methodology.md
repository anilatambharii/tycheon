# Benchmark methodology

This page says how the [leaderboard](leaderboard/index.md) is produced, what keeps it honest,
and what it cannot tell you. It is written to be read by someone who does not trust us.

**For research and risk analytics. Not investment advice.**

## What is being measured

For each series, forecast origin and horizon `h`, a model produces a distribution for the
log return `log(P[t+h] / P[t])`. Every model is scored at the *same* origins, in the same
walk-forward run, against what actually happened. Everything is read against the **random
walk**: the forecast "no change, with volatility estimated from the recent past".

The headline question is never "is this model's error small?". It is "is this model's loss
*distinguishably lower than doing nothing*?".

## Leakage controls

A backtest with lookahead is worse than none: it reports confident, plausible, wrong numbers.
The engine ([ADR 0006](adr/0006-walk-forward-evaluation.md)) makes lookahead raise instead of
inflating a score.

1. **Two clocks.** Every bar has a `timestamp` (what it is about) and an `available_at` (when
   it could first be known). A forecast made at origin `t` may use only bars with
   `available_at <= t`. A bar known exactly at the origin is allowed; one published a moment
   later is not.
2. **Guarded predict.** Every `predict` call is wrapped: the history passed in must have been
   published by the origin, and the `as_of` given must be the origin being scored.
   Violations raise `LookaheadError`, which the benchmark runner never catches. The result
   records how many guard checks were made.
3. **Embargo.** Anything *fitted* (the regime router, the conformal calibrator) is built from
   training history that ends at least `embargo` bars before the first origin it serves, and
   that is asserted. Outcomes of past forecasts used for fitting are also checked to be known
   by then.
4. **Refit schedule.** The router and calibrator are rebuilt at each fold from data available
   at that time. They are not tuned on the test window.
5. **A canary test.** The test suite poisons all data after a chosen origin (same timestamps,
   wildly different prices) and requires that forecasts at or before that origin are
   *bit-identical* for the baselines, the ensemble and the calibrated forecaster. A second test
   proves the canary itself can fail by running a forecaster that cheats.
6. **Non-overlapping origins.** Origins are at least `horizon` bars apart, so forecast errors
   are close to independent and the pooled Diebold-Mariano test is valid. Configs with
   overlapping origins are rejected.

What this does **not** control: if a *data vendor* silently restates history, a file that
omits `available_at` falls back to the conservative bar-end default, and that is only as good
as the file. Point-in-time correctness of real data is the data owner's responsibility.

## Baselines

The random walk is mandatory in every config; the runner refuses a config without it. Also
reported: a drift walk, a seasonal naive forecast, ARIMA and GARCH (the last two as volatility
and path baselines). All are real forecasters with the same interface and the same guards.

## Metrics

All computed in log-return space on the horizon-end return unless stated.

| Metric | Definition |
|---|---|
| MAE, RMSE | Of the median forecast. |
| MASE | MAE of the model divided by MAE of the no-change forecast on the same origins. Below 1 beats the random walk. |
| Directional accuracy | Share of origins where the median forecast and the outcome have the same sign. About 50% is what noise gives. |
| IC, RankIC | Pearson and Spearman correlation between forecast and realised returns across origins. Undefined (n/a) for a constant forecast such as the random walk's. |
| CRPS | Fair (ensemble-size-independent) estimate from sample paths. For quantile-only models, a trapezoid integral of pinball loss over the quantile grid, flagged with `*`; it ignores the tails beyond the outermost level, so it is slightly low. Exact and approximate values are never mixed in one comparison. |
| Interval coverage, width | Achieved share of outcomes inside the 50/80/90% central intervals, and their mean width. Closer to nominal is better; at equal coverage narrower is better. |
| QLIKE | Loss for the forecast *variance* against realised variance (sum of squared daily returns over the horizon); robust to noise in that proxy. |
| Diebold-Mariano | Test of equal expected loss against the random walk with the Harvey-Leybourne-Newbold small-sample correction. Run on squared error and on CRPS. |

A model "**beats the random walk**" only if *both* one-sided DM tests have p < 0.05, "**worse**"
only if both show it worse at that level, and otherwise "**indistinguishable**". Requiring both
is deliberately conservative: it makes a win harder to claim than a draw.

Pooled results stack the origins of all series. Pooled DM p-values treat series as independent;
correlated real series would make them too confident. On the synthetic data the series are
independent by construction.

## Calibration in the benchmark

`tycheon-ensemble` is the regime-weighted ensemble (random walk always a member).
`tycheon-calibrated` is the same ensemble, calibrated by adaptive conformal prediction fitted
on the ensemble's own *online* replay (weights chosen using only the past at each replayed
origin). The two are scored at the same origins, so the difference in coverage and CRPS is the
effect of calibration. Calibration is refit at every fold, from data before the embargo.

## The cost-aware diagnostic

A naive strategy takes a long, short or flat position for one horizon from the sign of the
median forecast, and pays half the quoted spread plus slippage (and any fee) on turnover. It
exists to ask whether an edge would survive frictions. It is **not** a trading system and not
a recommendation: Tycheon has no live execution in v1.

## Survivorship bias

A fixed list of symbols chosen today contains only survivors. For any real, non-point-in-time
universe the runner adds a survivorship warning to the results and the leaderboard. The
bundled data is synthetic, so no such bias applies; it is still labelled `static`.

## Synthetic data, and why

The repository contains no market data and Tycheon does not redistribute licensed data. The
reproducible leaderboard therefore runs on synthetic series with known structure (GBM, GARCH,
two-regime volatility, jumps). That has a useful consequence: **on a driftless random-walk
series no honest model can beat the random walk in expectation.** A model that appears to has
leaked or overfit. Results on synthetic data show the machinery behaves and show how each model
degrades when its assumptions are wrong; they say nothing about whether any model forecasts
real markets. To run on real data, supply your own licensed files (`--data-dir`), see the
[benchmark tutorial](tutorials/run-the-benchmark.md).

## When the baselines win

They often will, and the leaderboard says so. Each rendered section ends with a generated
**Where the baselines win** list: how many models beat the random walk, how many were
indistinguishable, which headline metrics a baseline leads. That list is computed from the
results; it cannot be edited to flatter a model.

## Limitations

- **Small samples.** A smoke-sized run has few origins; its p-values are wide. Do not read
  rankings between close models.
- **Multiple comparisons.** Many models are compared at once. Over many runs a model will
  appear to win by chance; treat isolated wins with suspicion.
- **Marginal calibration.** Conformal coverage holds on average over origins, not
  conditionally in the regime you are in now.
- **Horizon-end focus.** Headline metrics use the horizon-end return; intermediate steps are
  recorded in the raw results.
- **One data-generating process per series.** Real markets mix regimes, jumps and structural
  breaks that no synthetic series reproduces.
- **Software, not an audit.** The guards catch lookahead that goes through the history a
  forecaster is given. A forecaster that *reads data from somewhere else* is out of reach; the
  canary test exists to catch exactly that.

## Reproducing a result

Every result document records the Tycheon version, git commit, config hash, dataset-manifest
hash, environment and runtime. `python -m benchmarks.run --config <config> --execute`
regenerates it; synthetic data is generated from fixed seeds, so the same configuration gives
the same data and, up to floating-point and library-version differences, the same numbers.
