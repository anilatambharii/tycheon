# Tutorial: calibrated forecasts with Kronos

Kronos forecasts the path. This tutorial shows how to find out how much to trust the *width*
of its interval, and how to correct it when it is wrong, with measured evidence.

**For research and risk analytics. Not investment advice.**

## 1. Install and run

```bash
uv sync --extra kronos --extra report
uv run python examples/calibrated_kronos.py          # Kronos-mini on CPU, about 2.5 minutes
```

The first run downloads the Kronos-mini weights from Hugging Face. The script uses the
bundled **synthetic** series `SYN-REGIME`; for your own licensed data use a file provider (see
the [data layer](../models/index.md) and the [benchmark tutorial](run-the-benchmark.md)).

## 2. What the script does

```python
from tycheon.calibration import CalibratedForecaster
from tycheon.models.kronos import KronosForecaster

kronos = KronosForecaster(variant="mini")

# 1. Replay Kronos at past origins, using only what was known at each one,
#    and fit an adaptive conformal calibrator on that record.
model = CalibratedForecaster.fit_on(
    kronos, history, as_of=as_of, horizon=5, n_origins=40, n_samples=20, max_history=256
)

# 2. Forecast. The result is Kronos's distribution with calibrated quantiles and paths,
#    plus the evidence for its status.
forecast = model.predict(history.iloc[-256:], 5, 20, as_of)
print(forecast.summary())
forecast.calibration  # CalibrationInfo: method, n_scores, holdout coverage, notes
```

`history` ends at `as_of`: Tycheon refuses any data published after it
(`LookaheadError`). Calibration data published after the forecast's `as_of` is refused too.

## 3. Reading the output

One run on the synthetic series looked like this:

```text
            status        90% interval at the horizon        width
raw Kronos  uncalibrated  [    67.08,     70.39]              3.30
calibrated  calibrated    [    63.78,     72.68]              8.90
realised close at the horizon: 75.46

calibrated with 40 replayed forecasts (adaptive-conformal); evidence on 10 held-out forecasts:
   50% interval: raw Kronos covered 48.0%, calibrated covered 64.0%
   80% interval: raw Kronos covered 62.0%, calibrated covered 94.0%
   90% interval: raw Kronos covered 76.0%, calibrated covered 98.0%
   95% interval: raw Kronos covered 84.0%, calibrated covered 100.0%
all realised bars inside the calibrated 90% interval: False
```

Three things to take from it, in order of importance:

1. **The evidence, not the picture.** The holdout lines say how often each interval contained
   the outcome on forecasts the calibrator had not seen. Raw Kronos-mini's "90%" interval
   covered 76% here, so it was overconfident; the calibrated one covered more than 90%.
2. **The width changed a lot.** The calibrated interval is about 2.7 times wider. That is the
   price of honesty, not a defect.
3. **One forecast proves nothing.** The realised close (75.46) fell outside even the
   calibrated 90% interval. That is possible and expected about one time in ten.

## 4. Why "calibrated" here is weak evidence

The holdout has only 10 forecasts, so the status test's tolerance is wide (about two standard
errors). The script uses few origins to stay fast. For anything you intend to rely on, replay
at least a few hundred origins (`--n-origins 300`; expect Kronos-mini to take a few seconds per
origin on CPU) and read the *holdout coverage* and its tolerance, not just the label.

The status is one of:

| Status | Meaning |
|---|---|
| `calibrated` | On the holdout, achieved coverage was within tolerance of nominal. |
| `stale` | It was measured and it was *outside* tolerance: conditions changed. Treat intervals with caution. |
| `uncalibrated` | Too little history to say anything. The raw distribution is returned as is. |

## 5. Does the calibrated model beat the random walk?

Calibration fixes *coverage*, not *skill*. A forecast can have honest intervals and still be
no better than the random walk. The [leaderboard](../leaderboard/index.md) tests exactly
that with Diebold-Mariano tests, and on the synthetic data every model is statistically
indistinguishable from the random walk. Calibration tells you how much to trust the width;
the benchmark tells you whether the centre is worth anything.

## 6. Next

- Turn the calibrated forecast into risk numbers and a report: `examples/risk_report.py` and
  [risk](../risk.md).
- Calibrate the whole regime ensemble (random walk included): `tycheon-calibrated` in the
  [benchmark](run-the-benchmark.md).
- Read [calibration](../calibration.md) for the method and its limits (marginal, not
  conditional, coverage).
