# Calibration

Kronos forecasts the path; calibration tells you how much to trust its width. A model's own
interval is an opinion. A calibrated interval is one whose coverage has been *measured* on
forecasts the calibrator never saw.

## The mechanism

One mechanism, applied to any `ForecastDistribution`: for each quantile level and each
horizon step, an additive shift of that quantile in log-return space.

1. **Collect scores.** `collect_scores` replays the forecaster at past origins, using only
   data published by each origin, and records the realised outcome. The score for level
   `tau` is `y - q_tau`. The last usable origin is `horizon` bars before the end of the data,
   so every outcome used was already known at `as_of`. Origins are `horizon` bars apart by
   default so they do not overlap.
2. **Split conformal** (`SplitConformal`) shifts each quantile by the finite-sample conformal
   quantile of its scores (`ceil((n+1) tau)` for upper levels, `floor` for lower ones; if
   there are too few scores the level is left alone and noted). Because levels are shifted
   separately, a *biased* forecast is corrected, not just widened. An optional window uses
   only recent scores.
3. **Adaptive conformal** (`AdaptiveConformal`, ACI-style) tracks a working level
   `tau' += gamma * (tau - hit)` so it widens after misses and narrows after hits, which is
   what non-stationary volatility needs. Feedback is delayed realistically: an outcome only
   informs the calibrator once it has been published.
4. **Sample paths** are shifted by the same amounts with a rank-preserving, monotone map, so
   VaR/ES and drawdown computed from paths agree with the calibrated quantiles.

## The status is earned, not assigned

`ConformalCalibrator.fit` replays the method online and keeps the most recent origins as a
holdout. The status is:

- `calibrated`: on the holdout, the achieved coverage at the reference level is within
  `tolerance` of nominal (default `max(0.05, 2 * sqrt(c(1-c)/n_holdout))`, about two standard
  errors).
- `stale`: it was measured and it is outside that tolerance. Recent conditions differ from
  the calibration history. The warning in the risk report says so.
- `uncalibrated`: too few scores or holdout origins to say anything.

Levels the data cannot support (a 1% quantile needs about 99 scores) are left uncalibrated
and listed in `CalibrationInfo.notes`. The `CalibrationInfo` on every forecast carries the
method, the number of scores, `scores_as_of` (must not be after `as_of`; checked), holdout
size, the holdout coverage per level, and the same measurement for the raw forecast.

## What the synthetic tests show

Known-distribution tests (`tests/test_conformal.py`): on data with a known truth, calibrated
50/80/90/95% intervals hit their targets within 0.045 on unseen origins for both split and
adaptive methods, a mis-specified forecaster (wrong sigma, biased) is repaired, and under a
volatility shift (90% target) raw coverage was 0.58, pooled split 0.78, windowed split 0.90,
adaptive 0.90. These are synthetic results: they show the mechanism works when its
assumptions hold approximately, not that any real series is calibrated.

## Diagnostics

`evaluate_calibration` / `CalibrationReport`: empirical coverage (raw and calibrated, per
nominal level and per horizon step), mean interval width, PIT histogram, reliability curve,
quantile score and CRPS (fair estimator from samples). Plots (`reliability_svg`, `pit_svg`,
`coverage_svg`) need the `report` extra and return inline SVG.

## What it does not do

- Conformal coverage is *marginal*, not conditional: it does not promise coverage in the
  specific regime you are in right now.
- Exchangeability does not hold for financial returns; adaptive conformal and the holdout
  check exist because of that, and neither is a guarantee.
- Multi-asset portfolio risk built from calibrated marginals is **not** calibrated as a
  whole; see [risk](risk.md).

For research and risk analytics. Not investment advice.
