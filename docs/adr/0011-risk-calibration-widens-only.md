# ADR 0011: Calibration may only widen the tails used for risk figures

Status: accepted. For research and risk analytics. Not investment advice.

## Context
Conformal calibration (ADR 0004) moves a forecast's quantiles so that past intervals would have
covered the outcomes at their stated rate. That is the right thing for a forecast interval: too
narrow and too wide are both errors.

For a **risk figure** the two errors are not equal. Value at Risk and Expected Shortfall are read as
"how bad could it get", and a figure that is too small is the dangerous kind. A manual check of one
position (AAPL, 5 days, 5 years of real daily bars, 60 calibration origins) showed the problem:

| | VaR 95% | ES 95% | VaR 99% |
|---|---|---|---|
| calibration off | 5.38% | 6.86% | 7.57% |
| calibration on | **3.93%** | 6.83% | 9.05% |
| Gaussian check from the stated volatility | 5.56% | 6.97% | |

Calibration cut VaR 95% by about 27%. The calibrator had learned from only 60 recent origins, during
which the stock rose, so the lower tail had rarely been breached; it pulled the lower quantiles in
and nudged the median up. The "calibrated" status still held, because its tolerance is about 15
points of coverage on a 15-point holdout. Nothing was wrong with the arithmetic; the danger is that a
short run of calm or rising prices makes the headline risk number look better than it is.

## Decision
Risk calculations calibrate with `widen_only=True`
(`ConformalCalibrator(widen_only=True)`, `tycheon.calibration.conformal.outward_only`):

* quantile levels below the median may only move **down**, levels above only **up**, and the median
  level does not move;
* the clamp is applied to the replayed shifts **before** the holdout is evaluated, so the reported
  coverage and the `calibrated` / `stale` status describe the intervals that are actually served;
* sample paths are shifted by the same clamped shifts, so VaR, ES, drawdown and stress agree;
* the risk response carries a warning saying so.

Forecast and calibration endpoints are unchanged: a forecast interval is still calibrated in both
directions, and the calibration page still shows the unrestricted raw-versus-calibrated comparison.

## Consequences
* With the same seed, a risk figure is never smaller than the uncalibrated one. This is tested.
* Risk intervals can be conservative: if a model is too wide, calibration will not tighten it, and
  coverage will exceed nominal. That is the intended trade.
* The reported calibration status for risk can be `stale` more often than before, because a
  conservative interval over-covers; read it as "wider than needed", not "broken".
* A one-sided workaround for shorts and longs is not needed: both tails are protected symmetrically.

## Not done, and why
* **A longer calibration window** (more than 60 origins) would reduce the noise that caused this,
  but costs more compute per request and is a separate decision.
* **Showing raw and calibrated risk side by side** in the report is a good idea and a UI change.
* **A stricter `calibrated` tolerance** changes the meaning of a public status for forecasts too.
  Revisit with real-data evidence.
