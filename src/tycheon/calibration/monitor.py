"""Scheduled calibration monitoring: do the intervals still hold against what then happened?

Served-forecast metrics only *proxy* calibration decay (they see what was served, not what
the market then did). This module closes that gap by **replaying** a forecaster over the most
recent realised bars: at each past origin it forecasts using only data known then, scores the
outcomes that followed, fits the conformal calibrator on the older origins and measures
coverage and CRPS on the newest ones, which the calibrator never saw. Run it on a schedule
(a cron job, a Kubernetes CronJob, ``nightly-calibration.yml``) and publish the result as
Prometheus text so an alert can fire on real out-of-sample drift.

Point-in-time rules are inherited from :func:`~tycheon.calibration.scores.collect_scores`:
every bar must be known at ``as_of`` and every outcome is published by the origin it scores.

For research and risk analytics. Not investment advice.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tycheon.calibration.conformal import SplitConformal
from tycheon.calibration.forecaster import CalibratedForecaster
from tycheon.calibration.scores import collect_scores
from tycheon.data.asof import as_utc

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import pandas as pd

    from tycheon.models.base import Forecaster

__all__ = ["MonitorRow", "evaluate_series", "main", "to_prometheus"]


@dataclass(frozen=True)
class MonitorRow:
    """One (series, model, horizon) evaluation on the newest held-out origins."""

    series: str
    model_id: str
    horizon: int
    status: str
    n_scores: int
    n_holdout: int
    reference: float
    coverage_raw: float | None
    coverage_calibrated: float | None
    crps_raw: float | None
    crps_calibrated: float | None
    coverages: tuple[tuple[float, float, float], ...]  # (nominal, raw, calibrated)

    @property
    def gap(self) -> float | None:
        """``|reference - calibrated coverage|``, or ``None`` when nothing could be scored."""
        if self.coverage_calibrated is None:
            return None
        return abs(self.reference - self.coverage_calibrated)

    def to_dict(self) -> dict[str, object]:
        return {
            "series": self.series,
            "model_id": self.model_id,
            "horizon": self.horizon,
            "status": self.status,
            "n_scores": self.n_scores,
            "n_holdout": self.n_holdout,
            "reference_coverage": self.reference,
            "coverage_raw": self.coverage_raw,
            "coverage_calibrated": self.coverage_calibrated,
            "coverage_gap": self.gap,
            "crps_raw": self.crps_raw,
            "crps_calibrated": self.crps_calibrated,
            "coverages": [{"nominal": n, "raw": r, "calibrated": c} for n, r, c in self.coverages],
        }


def evaluate_series(
    forecaster: Forecaster,
    bars: pd.DataFrame,
    *,
    series: str,
    as_of: datetime,
    horizon: int,
    n_origins: int = 80,
    n_samples: int = 100,
    reference_coverage: float = 0.9,
    seed: int = 0,
) -> MonitorRow:
    """Replay ``forecaster`` on ``bars`` and score it on the newest origins.

    Raises:
        LookaheadError: ``bars`` contains data published after ``as_of``.
        DataValidationError: too few bars for ``horizon`` and ``n_origins``.
    """
    scores = collect_scores(
        forecaster,
        bars,
        as_of=as_utc(as_of),
        horizon=horizon,
        n_origins=n_origins,
        n_samples=n_samples,
        seed=seed,
    )
    fitted = CalibratedForecaster.from_scores(
        forecaster, scores, method=SplitConformal(), reference_coverage=reference_coverage
    )
    cal = fitted.calibrator
    status, achieved, _tolerance = cal.status()
    report = cal.report
    assert report is not None  # fit() always sets it  # noqa: S101
    ref = report.coverage_at(reference_coverage)
    return MonitorRow(
        series=series,
        model_id=forecaster.model_id,
        horizon=horizon,
        status=status,
        n_scores=report.n_scores,
        n_holdout=report.n_holdout,
        reference=reference_coverage,
        coverage_raw=None if ref is None else ref.raw,
        coverage_calibrated=achieved,
        crps_raw=report.crps_raw,
        crps_calibrated=report.crps_calibrated,
        coverages=tuple((c.nominal, c.raw, c.calibrated) for c in report.coverages),
    )


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def to_prometheus(rows: Sequence[MonitorRow]) -> str:
    """Render rows in the Prometheus text exposition format (for a textfile collector)."""
    lines: list[str] = []

    def family(name: str, doc: str) -> None:
        lines.extend([f"# HELP {name} {doc}", f"# TYPE {name} gauge"])

    def emit(name: str, row: MonitorRow, value: float | None, extra: str = "") -> None:
        if value is None:
            return
        labels = (
            f'series="{_label(row.series)}",model="{_label(row.model_id)}",'
            f'horizon="{row.horizon}"{extra}'
        )
        lines.append(f"{name}{{{labels}}} {value:.10g}")

    family(
        "tycheon_eval_coverage",
        "Out-of-sample coverage of calibrated intervals on the newest replayed origins",
    )
    for row in rows:
        for nominal, _raw, calibrated in row.coverages:
            emit("tycheon_eval_coverage", row, calibrated, f',nominal="{nominal:g}"')
    family("tycheon_eval_coverage_gap", "|reference - achieved coverage| on the newest origins")
    for row in rows:
        emit("tycheon_eval_coverage_gap", row, row.gap)
    family("tycheon_eval_crps", "Mean CRPS on the newest origins, in log-return units")
    for row in rows:
        emit("tycheon_eval_crps", row, row.crps_calibrated, ',kind="calibrated"')
        emit("tycheon_eval_crps", row, row.crps_raw, ',kind="raw"')
    family(
        "tycheon_eval_calibrated", "1 when the holdout says calibrated, 0 for stale or uncalibrated"
    )
    for row in rows:
        emit("tycheon_eval_calibrated", row, 1.0 if row.status == "calibrated" else 0.0)
    family("tycheon_eval_last_run_timestamp_seconds", "Unix time of the last evaluation run")
    lines.append(f"tycheon_eval_last_run_timestamp_seconds {datetime.now(UTC).timestamp():.0f}")
    return "\n".join(lines) + "\n"


def _forecaster(name: str) -> Forecaster:
    from tycheon.models.baselines import (  # noqa: PLC0415
        DriftForecaster,
        GARCHForecaster,
        RandomWalkForecaster,
    )

    registry: dict[str, Callable[[], Forecaster]] = {
        "random-walk": RandomWalkForecaster,
        "drift": DriftForecaster,
        "garch": GARCHForecaster,
    }
    if name not in registry:
        raise SystemExit(f"unknown model {name!r}; choose from {sorted(registry)}")
    return registry[name]()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``python -m tycheon.calibration.monitor`` (bundled synthetic series by default)."""
    parser = argparse.ArgumentParser(
        prog="tycheon-calibration-monitor", description=(__doc__ or "").split("\n\n")[0]
    )
    parser.add_argument("--symbols", nargs="+", default=["SYN-GBM", "SYN-GARCH", "SYN-REGIME"])
    parser.add_argument("--models", nargs="+", default=["random-walk"])
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--origins", type=int, default=80)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--reference", type=float, default=0.9)
    parser.add_argument("--out", type=Path, default=Path("calibration-monitor"))
    parser.add_argument(
        "--max-gap",
        type=float,
        default=None,
        help="exit 1 if any series' coverage gap exceeds this (e.g. 0.1)",
    )
    args = parser.parse_args(argv)

    from tycheon.data.sample import load_sample, sample_end  # noqa: PLC0415

    rows: list[MonitorRow] = []
    for symbol in args.symbols:
        bars = load_sample(symbol)
        as_of = sample_end(symbol)
        rows.extend(
            evaluate_series(
                _forecaster(model),
                bars,
                series=symbol,
                as_of=as_of,
                horizon=args.horizon,
                n_origins=args.origins,
                n_samples=args.samples,
                reference_coverage=args.reference,
            )
            for model in args.models
        )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "calibration.prom").write_text(to_prometheus(rows), encoding="utf-8")
    (args.out / "calibration.json").write_text(
        json.dumps(
            {
                "disclaimer": "For research and risk analytics. Not investment advice.",
                "data": "bundled synthetic series (tests the machinery, not any market)",
                "rows": [r.to_dict() for r in rows],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for r in rows:
        gap = "n/a" if r.gap is None else f"{r.gap:.3f}"
        print(f"{r.series:12s} {r.model_id:12s} h={r.horizon} {r.status:12s} gap={gap}")  # noqa: T201
    unscored = [r for r in rows if r.gap is None]
    worst = max((r.gap for r in rows if r.gap is not None), default=0.0)
    if args.max_gap is not None and (unscored or worst > args.max_gap):
        # No evidence is not a pass: a series that could not be scored fails the gate.
        reason = (
            f"{len(unscored)} series could not be scored (too little history)"
            if unscored
            else f"coverage gap {worst:.3f} exceeds --max-gap {args.max_gap}"
        )
        print(reason, file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
