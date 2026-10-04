# 0003. Point-in-time data and the forecast contract

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** T1

## Context

AGENTS.md makes three rules non-negotiable at the data and model boundary: every read
is point-in-time and refuses data published after `as_of`; every forecast carries
uncertainty, calibration status, model mix, `as_of` and a model-card reference; and
every evaluation is read against a random-walk baseline. Phase T1 is where those
stop being prose, so the representation has to make the wrong thing hard to write,
not merely forbidden.

Lookahead is the failure to design against, because it is silent: a leaky pipeline
produces confident, plausible, wrong numbers. The usual sources are a single
timestamp per observation (so "known at" and "about" are conflated), vendor
restatements that overwrite history, split- and dividend-adjusted prices that embed
future corporate actions, and naive timestamps that are ambiguous by up to a day.

## Decision

### Data: two clocks, append-only, refuse the future

1. **Every bar has two times.** `timestamp` is the bar's own open time;
   `available_at` is when it could first have been known. All guards compare
   `available_at` with `as_of`. Where a source gives no publication time the default
   is the *conservative* one, `timestamp + bar duration (+ a configurable lag)`: a
   daily bar is not knowable before its day ends.
2. **The store is append-only and bi-temporal.** A restatement is a new row for the
   same `timestamp` with a later `available_at`. The as-of query returns the latest
   version known at `as_of` (`QUALIFY row_number() ... ORDER BY available_at DESC`).
   Parquet files hold the data and DuckDB queries them; every value reaches the query
   as a bound parameter.
3. **Asking for the future is an error, not a smaller answer.** A request whose `end`
   is after `as_of` raises `LookaheadError` instead of being truncated. Naive
   timestamps are refused everywhere: `as_of` must be timezone-aware.
4. **Guards live in the base class, and again at the boundary.**
   `ProviderBase.fetch_bars` normalises, windows, filters and re-checks, so an adapter
   author cannot forget the guard; `AsOfStore.ingest` re-checks anything a provider
   returns, because a provider that does not subclass `ProviderBase` has none.
5. **Adjustment is point-in-time too.** `adjust_bars` applies only corporate actions
   that were both announced (`available_at`) and effective (`ex_date`) by `as_of`.
6. **Keys and paths are allow-listed.** Symbols become directory and file names, so
   they are matched against a strict pattern rather than sanitised.
7. **Providers are pluggable and the licence is the customer's.** No market data is
   bundled; the sample datasets are synthetic, deterministic and redistributable by
   construction. `yfinance` is development-only: double-gated, never in a hosted
   environment, warns on use, and absent from the lockfile. A licensed-vendor seam
   takes the customer's own key and never prints it.

### Forecasts: uncertainty is the return type

1. **`ForecastDistribution` is the only thing a forecaster returns.** It always holds
   quantiles; models that can sample also hold joint sample paths (`samples`, shape
   `(n_samples, horizon)`). There is no point-forecast return type to fall back to.
2. **It carries its provenance.** `as_of`, `last_observation`, calibration status
   (`uncalibrated` until Phase T2), `model_mix`, a model-card path, model version,
   context length used, seed and sampling parameters. Construction fails without a
   model card or with a mix that does not sum to 1. Arrays are copied and read-only.
3. **Quantile-only models do not get invented paths.** TimesFM and Chronos emit
   marginal quantiles per step; they say nothing about how steps move together. Their
   `samples` is `None`, and `require_paths()` refuses path-dependent questions
   (drawdown probability, horizon expected shortfall) instead of assuming a
   correlation structure. Marginal quantiles that cross are repaired by sorting and
   the repair is recorded in the diagnostics.
4. **The guard runs before the model.** `BaseForecaster.predict` validates the history
   against `as_of` before any model code executes; a test asserts the model is not
   even called when the history leaks.
5. **Every forecaster ships a card.** A contract test requires the card file to exist
   with the required sections and the disclaimer.

## Consequences

**Good.** A leaky history is rejected at three layers (provider, store, forecaster)
and there is an end-to-end test that late-arriving bars and restatements cannot change
an earlier forecast, run against every forecaster. Restatements are representable, so
backtests can later be reproduced as of any date.

**Costly.** Callers must supply `as_of` and timezone-aware timestamps; that friction is
the point. The conservative availability default makes some data look slightly staler
than it was. Bi-temporal storage keeps every revision, so it grows. Intraday series
with market-hours gaps get approximate future *labels* (values never depend on them
except through Kronos' calendar features).

**Open.** Without real publication times, a late restatement of a bar cannot be
detected from a bare frame; only data read through the store carries `available_at`
end to end. Calibration (T2) and evaluation (T3) will consume this contract.

## Alternatives considered

- **One timestamp per bar.** Simpler, and exactly the representation that conflates
  "about" with "known at".
- **Overwrite on restatement.** Smaller, and makes every historical read depend on
  today's vendor state.
- **Return a point forecast plus an optional interval.** Optional uncertainty becomes
  absent uncertainty in practice.
- **Fabricate sample paths from quantiles** (for example with a copula). Gives
  TimesFM and Chronos a `samples` array at the price of an assumed dependence
  structure that nobody measured; every drawdown number built on it would inherit the
  assumption silently.
- **Truncate a window that reaches past `as_of`.** Convenient, and hides the bug that
  asked for the future.
