"""Calibrated forecasts with Kronos: how much to trust the width of its interval.

    uv sync --extra kronos --extra report
    uv run python examples/calibrated_kronos.py                  # Kronos-mini, CPU
    uv run python examples/calibrated_kronos.py --variant small

Kronos forecasts the path; Tycheon tells you how much to trust it. This script:

1. reads one series point-in-time and holds out the last ``horizon`` bars;
2. asks Kronos for a forecast as of the cut (the *raw*, uncalibrated distribution);
3. replays Kronos at past origins (using only what was known then) and fits an adaptive
   conformal calibrator on that record;
4. prints the raw and calibrated intervals side by side, with the calibration status and the
   holdout coverage that justify it, and what actually happened.

One forecast proves nothing. The evidence is the *holdout coverage*: how often the interval
contained the outcome on forecasts the calibrator had not seen. The data is synthetic (the
bundled sample series); real use needs your own licensed data.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, cast

import numpy as np

from tycheon.calibration import CalibratedForecaster
from tycheon.data.sample import load_sample
from tycheon.models.base import DISCLAIMER
from tycheon.models.kronos import KronosForecaster

if TYPE_CHECKING:
    from tycheon.models.kronos.specs import Variant


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="SYN-REGIME")
    parser.add_argument("--variant", choices=["mini", "small", "base"], default="mini")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--n-origins", type=int, default=40, help="past origins to replay")
    parser.add_argument("--n-samples", type=int, default=20, help="paths per Kronos forecast")
    args = parser.parse_args()

    bars = load_sample(args.symbol)
    cut = len(bars) - args.horizon
    history, held_out = bars.iloc[:cut], bars.iloc[cut:]
    as_of = history["available_at"].iloc[-1]
    print(f"{args.symbol}: forecasting {args.horizon} bars ahead as of {as_of.date()}")

    kronos = KronosForecaster(variant=cast("Variant", args.variant))
    raw = kronos.predict(history.iloc[-256:], args.horizon, args.n_samples, as_of, seed=1)

    print(f"replaying Kronos at {args.n_origins} past origins to calibrate ...")
    calibrated_model = CalibratedForecaster.fit_on(
        kronos,
        history,
        as_of=as_of,
        horizon=args.horizon,
        n_origins=args.n_origins,
        n_samples=args.n_samples,
        max_history=256,
    )
    calibrated = calibrated_model.predict(
        history.iloc[-256:], args.horizon, args.n_samples, as_of, seed=1
    )

    realized = held_out["close"].to_numpy()
    last = args.horizon - 1
    print()
    print(f"{'':<12}{'status':<14}{'90% interval at the horizon':<32}{'width':>8}")
    for label, dist in (("raw Kronos", raw), ("calibrated", calibrated)):
        lo, hi = dist.interval(0.9)
        print(
            f"{label:<12}{dist.calibration_status:<14}"
            f"[{lo[last]:>9.2f}, {hi[last]:>9.2f}]{'':<10}{hi[last] - lo[last]:>8.2f}"
        )
    print(f"realised close at the horizon: {realized[last]:.2f}")

    info = calibrated.calibration
    if info is None:
        print("\nthe calibrator had too little history to calibrate: the forecast is uncalibrated")
    else:
        print(
            f"\ncalibrated with {info.n_scores} replayed forecasts "
            f"({info.method}); evidence on {info.holdout_n} held-out forecasts:"
        )
        for nominal in sorted(info.holdout_coverage):
            raw_cov = info.raw_holdout_coverage[nominal]
            print(
                f"  {nominal:>4.0%} interval: raw Kronos covered {raw_cov:>5.1%}, "
                f"calibrated covered {info.holdout_coverage[nominal]:>5.1%}"
            )
        for note in info.notes:
            print(f"  note: {note}")
    inside = bool(
        np.all(
            (realized >= calibrated.interval(0.9)[0]) & (realized <= calibrated.interval(0.9)[1])
        )
    )
    print(f"all realised bars inside the calibrated 90% interval: {inside}")
    print(f"\n{DISCLAIMER}")


if __name__ == "__main__":
    main()
