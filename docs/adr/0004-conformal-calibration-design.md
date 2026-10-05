# 0004. Conformal calibration design

- **Status:** Accepted
- **Date:** 2026-10-05
- **Phase:** T2

## Context

Phase T2 turns any `ForecastDistribution` into intervals with *measured* coverage. Financial
returns are neither independent nor stationary, so classical split conformal is only
approximate, and any claim of "calibrated" must be backed by out-of-sample evidence.

## Decision

1. **One mechanism: per-level, per-step additive quantile shifts in log-return space.**
   The score is `y - q_tau`, one set per level and horizon step. Asymmetric shifts correct
   bias, not just width. The same shifts are applied to sample paths through a monotone,
   rank-preserving map so path-based risk agrees with the quantiles.
2. **Two methods behind one interface:** `SplitConformal` (pooled, optional window) and
   `AdaptiveConformal` (ACI-style, `gamma=0.02`) for non-stationary data. Feedback is delayed
   by outcome publication time, so no outcome is used before it was knowable.
3. **Status is earned on a holdout.** The most recent origins are replayed with only what
   was known at each, and the status (`calibrated`, `stale`, `uncalibrated`) follows from
   achieved versus nominal coverage within a tolerance of about two standard errors.
   `CalibrationInfo` on the forecast carries the evidence; the type refuses a status without
   it and refuses `scores_as_of` after `as_of`.
4. **Unsupported levels stay uncalibrated, with a note**, instead of being extrapolated.
5. **Scores are collected at non-overlapping origins** by default. A smaller stride gives
   more but dependent scores and the effective count is noted.
6. **Plain in-house implementation** (MAPIE allowed as a reference by AGENTS.md); no new
   dependency beyond optional matplotlib for plots.

## Consequences

- Calibration costs `n_origins` forecasts per model; slow models need a budget.
- Marginal coverage only. A calibrated label is evidence about the recent past, not a promise.
- The covariate corrector uses LightGBM's native API because its sklearn API would add
  scikit-learn, which is not otherwise needed.

## Alternatives considered

Symmetric absolute-residual conformal (cannot correct bias); CQR with a learned quantile
model (needs a training pipeline per model); Bayesian recalibration (more assumptions).
