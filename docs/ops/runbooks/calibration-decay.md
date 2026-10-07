# Runbook: calibration decay (`TycheonUncalibratedShareHigh`, `TycheonCoverageGapHigh`)

**Meaning:** the forecasts being served are less often calibrated, or their intervals are
further from their nominal coverage on the holdout, than they should be. These alerts are *proxies*
(see [SLOs](../slos.md)): they observe what was served, not what then happened in the market.

## Triage
1. **Is it one model?** Split `tycheon_forecast_calibration_total` by `model_family` and `status`.
   * A rise in `uncalibrated` for one family usually means that model's calibration is failing to fit
     (too little history, or data that breaks the exchangeability the conformal method relies on).
   * A rise for **all** families points at the data: a stale feed (customers' CSV not updated),
     a market regime change, or a bug in the data path.
2. **Is it customer opt-out?** Requests with `calibrate=false` count as uncalibrated. A customer
   scripting that flag moves the ratio without any model problem. Check the share of requests with it.
3. **Is it a deploy?** A release that touched `calibration/`, `services/` or a model changes status
   rates immediately. Compare with the deploy time; roll back if it matches.
4. **Is it the world?** In a volatility spike or regime change, conformal intervals widen or lose
   coverage by design. The honest response is to say so, not to hide it: the status field already
   tells each customer, per forecast, whether it was calibrated, stale or uncalibrated.

## Actions
* Run the evaluation on realised data: `make benchmark-small` (the leaderboard harness) against
  the affected series, and compare coverage with the last published run.
* If calibration is genuinely degraded, recalibrate (refit on recent origins) or widen the window,
  and note it in the model card. Do **not** silently relax the tolerance.
* If a data feed is stale, tell the affected customers: stale data is their licence and their feed.

## Remember
A calibrated interval is a statement about long-run coverage under assumptions, not a guarantee.
Nothing here is investment advice.
