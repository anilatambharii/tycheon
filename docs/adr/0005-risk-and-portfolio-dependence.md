# 0005. Risk from sample paths, and portfolio dependence

- **Status:** Accepted
- **Date:** 2026-10-05
- **Phase:** T2

## Context

Risk measures need joint sample paths, but a portfolio's assets are forecast one at a time.
Something has to supply their dependence, and whatever it is cannot be calibrated by the
per-asset machinery.

## Decision

1. **All risk is computed from joint paths** (`RiskInput`), which carry the calibration
   status, model mix and `as_of` of the forecast they came from.
2. **Dependence is documented and assumed:** shrunk correlation from data known at `as_of`,
   imposed with a Gaussian copula by rank reordering, preserving every asset's marginal
   paths. Two couplings are offered. `terminal` (default) keeps each path intact but
   understates interim co-movement (step-k correlation about `rho * k / H`); `stepwise` imposes
   the correlation at every step but breaks serial dependence. Both limits are written into
   the notes of every report.
3. **A multi-asset portfolio is always `uncalibrated`**, whatever its marginals say. A
   one-asset portfolio keeps the asset's calibration.
4. **Uncertainty about the risk numbers is shown:** bootstrap standard errors, tail path
   counts, a `reliable` flag, Wilson intervals.
5. **Stress scenarios without a probability say so.** Historical and shock scenarios are
   not given probabilities; the model-implied tail inherits the forecast's calibration.
6. **The report is self-contained:** inline SVG, no scripts, no external resources,
   escaped dynamic text, and plain-language warnings placed first.

## Consequences

- Portfolio tail risk is probably understated in crashes (no tail dependence). The report
  states it rather than hiding it, and `stress_correlation` lets a reader probe it.
- Joint paths are required: quantile-only forecasters (TimesFM, Chronos, the ensemble in
  `quantile` mode) cannot feed path-based risk.

## Alternatives considered

Empirical copula or vine copulas (more parameters than the data supports at these sample
sizes; a later phase can replace the coupler behind the same interface); modelling the
portfolio as one asset (needs a portfolio history and hides the per-asset calibration).
