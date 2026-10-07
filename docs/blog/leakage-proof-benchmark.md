<!-- DRAFT: for maintainer review. Not part of the published site (docs/blog/ is excluded in mkdocs.yml).
Maintainer notes: (1) all numbers are copied from benchmarks/results/small/0.1.0.json; re-check them if that file is regenerated.
(2) In that file the random walk's own MASE is 1.014, not exactly 1.0; this draft reports it as is and has not traced why.
(3) Replace the file paths with permalinks to a release tag before publishing. -->

# A leakage-proof benchmark for financial foundation models

*For research and risk analytics. Not investment advice.*

Most backtests of forecasting models are wrong in the same way: somewhere, information from the future
reached the model. A feature was computed with a full-sample mean. A revised number replaced the one that
was actually published on the day. Training data ended one bar before the first test point, and the label
overlapped it. The result is a confident, plausible, wrong score, which is worse than no score.

Time-series foundation models make this easier to get wrong, not harder. They are large, they ship
pre-trained, and a researcher evaluating one usually has a pandas frame and a loop. This post describes how
the [Tycheon](https://github.com/anilatambharii/tycheon) benchmark tries to make the wrong thing raise an
exception instead of inflating a number, shows the results it currently produces, and says what those
results do not show. Spoiler: on the data we can publish, nothing beats the random walk.

## Two clocks, not one

Every bar in Tycheon has a `timestamp` (what it is about) and an `available_at` (when it could first be
known). A daily bar stamped Monday is not knowable on Monday morning. When a file does not say, the schema
defaults to the conservative choice: the bar's close plus any lag. The test
`test_availability_defaults_to_the_conservative_bar_close` in `tests/test_data_schema.py` pins this, and
`test_normalize_refuses_to_guess_availability` pins that the loader will not invent a bar duration to make
the data look cleaner than it is.

Every read then takes an `as_of`. In `src/tycheon/data/asof.py`:

- `require_not_future` refuses a request whose window ends after `as_of`. Asking for the future is a caller
  bug, so it raises `LookaheadError` rather than quietly truncating (the truncation would hide the bug).
- `assert_available` raises unless every row's `available_at` is at or before `as_of`.
- `filter_as_of` keeps rows known at `as_of` and, where a bar was revised, keeps the latest version known
  *then*, not the version the vendor shows today.

The restatement behaviour is tested directly: `test_a_restatement_is_invisible_until_it_was_published` and
`test_bars_not_yet_available_do_not_exist_at_as_of` in `tests/test_data_store.py`, plus
`test_ingest_does_not_trust_a_provider_that_returns_the_future`, which hands the store a provider that
misbehaves. These carry the `@pytest.mark.leakage` marker, which the repository's contribution rules require
for every data path.

The forecast side has the same contract. A forecaster called with a history that includes anything published
after `as_of` raises before the model runs. The README quickstart shows it with a real error message.

## Guards inside the walk-forward loop

The evaluation engine is `walk_forward` in `src/tycheon/backtest/walk_forward.py`, configured by
`WalkForwardConfig`. Four things matter.

**Guarded predict.** Each forecaster is wrapped in `GuardedForecaster`
(`src/tycheon/backtest/guards.py`). On every `predict` call it checks two things: that the history passed in
was published by the origin (`assert_known_by`), and that the `as_of` passed in is the origin the engine
assigned. The second check stops a model from being handed a later `as_of` than the origin being scored. Bars
whose `available_at` equals the origin are allowed, since a strict inequality would discard the newest bar a
forecaster is entitled to; `test_a_bar_known_exactly_at_the_origin_is_allowed` pins both sides of that
boundary. Violations raise `LookaheadError`, and the benchmark runner never catches it
(`test_a_lookahead_error_is_never_swallowed` in `tests/test_benchmarks.py`). The result file records how many
guard checks ran; for Kronos-mini in the run below, 108.

**Embargo.** Anything that is fitted (the regime router, the conformal calibrator) is built from training
history that must end at least `embargo` bars before the first origin it serves.
`assert_fit_data_precedes` enforces it, `walk_forward` calls it for every fold, and
`test_fit_data_must_end_an_embargo_before_the_first_origin` checks the exact boundary: five bars apart is
allowed, four is not. The benchmark config loader also refuses a config with no embargo
(`test_embargo_is_mandatory`).

**Refit schedule.** The router and calibrator are rebuilt at each fold from data available at that time. They
are never tuned on the test window. The result file records 9 refits per model in the run below.

**Non-overlapping origins.** Origins are at least `horizon` bars apart, so forecast errors are close to
independent and the pooled Diebold-Mariano test is valid. A config with overlapping origins is rejected
(`test_overlapping_origins_are_refused`), and the random-walk baseline is mandatory in every config
(`test_random_walk_baseline_is_mandatory`).

## A canary that can fail

Guards catch lookahead that goes through the history a forecaster is handed. They cannot catch a forecaster
that reads data from somewhere else, and a guard can be buggy. So the test suite also runs a canary.
`assert_no_peeking` in `tests/test_backtest_walk_forward.py` runs the whole walk-forward twice. The second
time, every price after the middle origin is poisoned: same timestamps, same publication times, prices
multiplied by large random factors. Forecasts at or before that origin must be bit-identical between the two
runs (`np.testing.assert_array_equal`, not `allclose`). The test first asserts that the poison really changes
the later outcomes, otherwise it would prove nothing.

The canary runs on the random walk and drift baselines, on the regime ensemble together with its fitted
router, on a calibrated forecaster and on the calibrated ensemble (the four `test_canary_*` tests). And
because a test that cannot fail is decoration, `test_the_canary_catches_a_forecaster_that_cheats` runs a
forecaster that deliberately looks `horizon` bars ahead and requires the canary to detect it.

## What the benchmark produces

Every model is scored at the same origins, against the random walk, in log-return space, with the
Diebold-Mariano test (`diebold_mariano` in `src/tycheon/backtest/metrics.py`, two-sided with the
Harvey-Leybourne-Newbold small-sample correction) run on both squared error and CRPS. A model "beats the
random walk" only if both one-sided tests give p < 0.05. Requiring both makes a win harder to claim than a
draw.

The published run is `benchmarks/results/small/0.1.0.json`: benchmark `small`, 2026-10-05, CPU, Tycheon 0.1.0,
three synthetic series (GBM, GARCH, two-regime volatility), horizon 5, 36 origins per series, 108 pooled.
The file records git commit `5c344b4660` with uncommitted changes.

| Model | MASE vs RW | RMSE (%) | CRPS (%) | 90% coverage | DM p, sq. error | DM p, CRPS | Outcome |
|---|--:|--:|--:|--:|--:|--:|---|
| random-walk (baseline) | 1.014 | 3.229 | 1.752 | 79.6% | n/a | n/a | reference |
| drift (baseline) | 1.024 | 3.264 | 1.778 | 79.6% | 0.850 | 0.905 | indistinguishable |
| seasonal-naive (baseline) | 1.012 | 3.244 | 1.738 | 85.2% | 0.618 | 0.269 | indistinguishable |
| garch (baseline) | 1.028 | 3.267 | 1.734 | 83.3% | 0.929 | 0.258 | indistinguishable |
| kronos-mini (zero-shot) | 1.025 | 3.306 | 1.819 | 57.4% | 0.685 | 0.756 | indistinguishable |
| tycheon-ensemble | 1.041 | 3.298 | 1.744 | 83.3% | 0.991 | 0.343 | indistinguishable |
| tycheon-calibrated | 1.039 | 3.281 | 1.781 | 86.1% | 0.787 | 0.797 | indistinguishable |

"DM p" is the one-sided p-value that the model has lower loss than the random walk. Nominal coverage is 90%.

The honest reading is short. No model beats the random walk. The best RMSE belongs to the random walk, the
best MASE to seasonal-naive, the best CRPS to GARCH: all baselines. Kronos-mini's raw 90% interval contained
57.4% of outcomes, so zero-shot intervals are overconfident on this data, which is the problem calibration
exists to address. The calibrated ensemble's 90% interval contained 86.1%, closest to nominal in the table, at
the cost of the widest interval; but that ensemble is made of baselines, not Kronos, so it says nothing yet
about calibrating Kronos. Kronos-mini also showed a 57.4% directional hit rate and an information coefficient
of 0.168. With 108 origins that is within what noise produces, and the Diebold-Mariano tests agree.

The leaderboard page (`docs/leaderboard/index.md`) is rendered from that JSON by `benchmarks/render.py`, and
ends each section with a generated "Where the baselines win" list that cannot be edited to flatter a model.

## What this does not show

This is the more important section.

**The data is synthetic.** The repository ships no market data and does not redistribute licensed data. The
reproducible leaderboard runs on generated series with known structure. That has one useful consequence: on a
driftless random-walk series no honest model can beat the random walk in expectation, so a model that appears
to has leaked or overfitted. It also means the run shows that the machinery behaves, and says nothing about
whether any model forecasts real markets.

**The sample is small.** 108 pooled origins from a smoke-sized run. The p-values are wide. Do not rank close
models. Many models are compared at once, so over many runs one will win by chance.

**Two models of interest are missing.** TimesFM and Chronos are not in this run.

**No live trading.** Tycheon has no live execution in v1. The benchmark report includes a cost-aware diagnostic
(a naive one-horizon sign strategy with spread and slippage), but it is a research diagnostic to ask whether an
edge would survive frictions, not a strategy and not a recommendation. We make no claim about returns.

**Regime dependence.** Real markets mix regimes, jumps and structural breaks that no synthetic series
reproduces. Conformal coverage holds on average over origins, not conditionally in whatever regime you are in
now.

**The guards have limits.** They catch lookahead that passes through the history a forecaster is given. If a
data vendor silently restates history, or a file omits `available_at` and falls back to the bar-end default,
correctness is only as good as the file. Point-in-time correctness of real data is the data owner's
responsibility. Pooled DM p-values treat series as independent, which holds by construction here and would
not for correlated real series.

**One more caveat on the canary.** It is a software check, not an audit. It shows that the tested forecasters
do not peek through the interfaces the engine controls.

## Reproduce it, break it

```bash
git clone https://github.com/anilatambharii/tycheon && cd tycheon
make setup
make benchmark-small      # python -m benchmarks.run --config benchmarks/configs/small.yaml --execute
uv run pytest tests/test_backtest_walk_forward.py -k canary
```

The Kronos-mini rows took 612.6 s of the 743.6 s total on a laptop CPU. Synthetic data comes from fixed
seeds, so the same configuration reproduces the same data and, up to floating-point and library-version
differences, the same numbers.

If you can build a forecaster that passes the canary and still sees the future, that is a vulnerability under
the project's security policy, not just a bug, because it silently inflates every result downstream. The
fastest way to improve this benchmark is to bring your own licensed data through the file provider
(`--data-dir`), run it, and publish what you find, including when the baseline wins.

*For research and risk analytics. Not investment advice. Nothing in this post is a recommendation to buy or sell
any security.*
