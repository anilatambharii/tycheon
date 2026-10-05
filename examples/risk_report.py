"""A calibrated risk report for a three-asset sample portfolio: JSON plus one HTML file.

    uv sync --extra report
    uv run python examples/risk_report.py                  # random walk, drift, GARCH
    uv run python examples/risk_report.py --kronos mini    # add Kronos (needs --extra kronos)

For each asset the script does what a real deployment would:

1. Replays every candidate forecaster at past origins, using only what was known then
   (``collect_scores``).
2. Learns, per volatility regime, how much to trust each candidate (the random walk is always
   a candidate), and replays that ensemble online so its own history is honest.
3. Calibrates the ensemble with adaptive conformal prediction, and keeps the most recent
   origins as a holdout that decides whether the result is called ``calibrated`` or ``stale``.
4. Aggregates the assets with a correlation estimated from data known at ``as_of``, and
   reports VaR, ES, drawdown probability, volatility, stress scenarios and diagnostics.

The data is **synthetic** (bundled sample series). Real use needs your own licensed data. A
portfolio of several assets is always reported ``uncalibrated``: the dependence between the
assets is assumed, not measured.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from tycheon.calibration import AdaptiveConformal, ConformalCalibrator, collect_scores
from tycheon.data.sample import load_sample, sample_end
from tycheon.models.baselines import DriftForecaster, GARCHForecaster, RandomWalkForecaster
from tycheon.risk import Portfolio, build_risk_report, estimate_correlation
from tycheon.routing import (
    RANDOM_WALK_ID,
    EnsembleForecaster,
    RegimeRouter,
    VolatilityRegimeDetector,
    labels_for_scores,
)

if TYPE_CHECKING:
    import pandas as pd

    from tycheon.calibration import CalibrationReport
    from tycheon.models.base import ForecastDistribution, Forecaster

KronosVariant = Literal["mini", "small", "base"]
SYMBOLS = ("SYN-GBM", "SYN-GARCH", "SYN-REGIME")
POSITIONS = {"SYN-GBM": 400_000.0, "SYN-GARCH": 350_000.0, "SYN-REGIME": 250_000.0}


def candidates(kronos_variant: str | None) -> dict[str, Forecaster]:
    members: dict[str, Forecaster] = {
        RANDOM_WALK_ID: RandomWalkForecaster(),
        "drift": DriftForecaster(window=250),
        "garch": GARCHForecaster(window=500),
    }
    if kronos_variant:
        from tycheon.models.kronos import KronosForecaster

        members[f"kronos-{kronos_variant}"] = KronosForecaster(
            variant=cast("KronosVariant", kronos_variant)
        )
    return members


def calibrated_forecast(
    bars: pd.DataFrame,
    members: dict[str, Forecaster],
    args: argparse.Namespace,
    as_of: pd.Timestamp,
) -> tuple[ForecastDistribution, CalibrationReport]:
    """Router, ensemble and conformal calibration for one asset, using data known at ``as_of``."""
    horizon = args.horizon
    scores = {
        name: collect_scores(
            model,
            bars,
            as_of=as_of,
            horizon=horizon,
            n_origins=args.n_origins,
            max_history=500,
            n_samples=60,
        )
        for name, model in members.items()
    }
    detector = VolatilityRegimeDetector(n_regimes=2, window=20, lookback=500, min_history=150)
    labels = labels_for_scores(detector, bars, next(iter(scores.values())))
    router = RegimeRouter()
    weights = router.fit(scores, labels)
    ensemble = EnsembleForecaster(members, detector, weights)

    history = router.replay(scores, labels)  # the ensemble's own honest score history
    calibrator = ConformalCalibrator(AdaptiveConformal()).fit(history)
    raw = ensemble.predict(bars, horizon, 1000, as_of, seed=1)
    assert calibrator.report is not None  # noqa: S101
    return calibrator.calibrate(raw), calibrator.report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--horizon", type=int, default=10, help="bars ahead")
    parser.add_argument("--n-origins", type=int, default=80, help="past origins to replay")
    parser.add_argument("--kronos", choices=["mini", "small", "base"], default=None)
    parser.add_argument("--out", type=Path, default=Path("examples/output"))
    args = parser.parse_args()

    as_of = sample_end(SYMBOLS[0])
    bars = {s: load_sample(s, as_of=as_of) for s in SYMBOLS}
    forecasts, reports = {}, {}
    for symbol in SYMBOLS:
        print(f"{symbol}: scoring candidates, routing, calibrating ...")
        forecasts[symbol], reports[symbol] = calibrated_forecast(
            bars[symbol], candidates(args.kronos), args, as_of
        )
        print(f"  {forecasts[symbol].summary()}")

    portfolio = Portfolio.from_values(POSITIONS, name="Sample three-asset portfolio")
    report = build_risk_report(
        portfolio=portfolio,
        forecasts=forecasts,
        bars_by_symbol=bars,
        as_of=as_of,
        calibration_reports=reports,
        correlation=estimate_correlation(bars, as_of=as_of),
        shocks={"All assets -10%": dict.fromkeys(SYMBOLS, -0.10)},
        title="Tycheon risk report (synthetic sample data)",
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "risk_report.json").write_text(report.to_json(), encoding="utf-8")
    (args.out / "risk_report.html").write_text(report.to_html(), encoding="utf-8")
    print(f"wrote {args.out / 'risk_report.json'} and {args.out / 'risk_report.html'}")
    for warning in report.data["warnings"]:
        print(f"  warning: {warning}")


if __name__ == "__main__":
    main()
