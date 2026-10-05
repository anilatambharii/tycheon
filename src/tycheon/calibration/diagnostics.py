"""Calibration diagnostics: does the stated uncertainty match what happened?

:func:`evaluate_calibration` compares, over the most recent origins, the **uncalibrated**
forecast with the **calibrated** one, where the calibrated shift at every origin is the one
that was actually available *at that origin* (computed only from outcomes already known
then). So the headline numbers are an honest out-of-sample record, not a fit to the data they
are scored on.

It reports empirical coverage and mean interval width at several nominal levels, PIT
histograms, a reliability curve, CRPS (from sample paths when the model has them) and the mean
pinball (quantile) score, which also works for quantile-only models.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from tycheon.calibration.metrics import (
    crps_samples,
    pit_histogram,
    quantile_score,
    shift_samples,
)

if TYPE_CHECKING:
    from tycheon.calibration.scores import ScoreSet

Floats = NDArray[np.float64]

DEFAULT_COVERAGES: tuple[float, ...] = (0.5, 0.8, 0.9, 0.95)


@dataclass(frozen=True)
class CoverageResult:
    """Achieved coverage of central intervals at one nominal level, raw versus calibrated."""

    nominal: float
    raw: float
    calibrated: float
    width_raw: float
    width_calibrated: float
    n_origins: int
    raw_by_step: Floats
    calibrated_by_step: Floats

    def to_dict(self) -> dict[str, object]:
        return {
            "nominal": self.nominal,
            "raw_coverage": self.raw,
            "calibrated_coverage": self.calibrated,
            "mean_width_raw": self.width_raw,
            "mean_width_calibrated": self.width_calibrated,
            "n_origins": self.n_origins,
            "raw_by_step": self.raw_by_step.tolist(),
            "calibrated_by_step": self.calibrated_by_step.tolist(),
        }


@dataclass(frozen=True)
class CalibrationReport:
    """What the holdout says about a calibrator. All widths are in log-return units."""

    method: str
    model_id: str
    n_scores: int
    n_holdout: int
    coverages: tuple[CoverageResult, ...]
    pit_bins: int
    pit_raw: NDArray[np.int64]
    pit_calibrated: NDArray[np.int64]
    reliability_levels: tuple[float, ...]
    reliability_raw: Floats
    reliability_calibrated: Floats
    quantile_score_raw: float
    quantile_score_calibrated: float
    crps_raw: float | None
    crps_calibrated: float | None

    def coverage_at(self, nominal: float) -> CoverageResult | None:
        for result in self.coverages:
            if abs(result.nominal - nominal) < 1e-9:
                return result
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "model_id": self.model_id,
            "n_scores": self.n_scores,
            "n_holdout": self.n_holdout,
            "coverages": [c.to_dict() for c in self.coverages],
            "pit_bins": self.pit_bins,
            "pit_raw": self.pit_raw.tolist(),
            "pit_calibrated": self.pit_calibrated.tolist(),
            "reliability_levels": list(self.reliability_levels),
            "reliability_raw": self.reliability_raw.tolist(),
            "reliability_calibrated": self.reliability_calibrated.tolist(),
            "quantile_score_raw": self.quantile_score_raw,
            "quantile_score_calibrated": self.quantile_score_calibrated,
            "crps_raw": self.crps_raw,
            "crps_calibrated": self.crps_calibrated,
        }


def _level_index(levels: tuple[float, ...], target: float) -> int | None:
    for j, level in enumerate(levels):
        if abs(level - target) < 1e-9:
            return j
    return None


def evaluate_calibration(
    scores: ScoreSet,
    delta_used: Floats,
    *,
    holdout: int,
    method: str,
    coverages: tuple[float, ...] = DEFAULT_COVERAGES,
    pit_bins: int = 10,
) -> CalibrationReport:
    """Score the last ``holdout`` origins, raw versus calibrated.

    Args:
        delta_used: ``(n, levels, horizon)`` the shift that was available at each origin
            (``NaN`` where too few scores were known yet). Origins with any missing shift
            needed for a given statistic are left out of that statistic.
    """
    n, levels = scores.n, scores.levels
    start = max(0, n - holdout)
    idx = np.arange(start, n)
    base, y, delta = scores.base_logq[idx], scores.realized[idx], delta_used[idx]
    # Levels the calibrator supports *now* (the latest origin). A tail the data cannot yet
    # support (a 1% level with 60 scores) must not block evaluating the levels it can.
    cols = np.isfinite(delta_used[-1]).all(axis=1) if n else np.zeros(len(levels), dtype=bool)
    levels_sel = tuple(level for level, ok in zip(levels, cols.tolist(), strict=True) if ok)
    base_s, delta_s = base[:, cols, :], delta[:, cols, :]
    usable = np.isfinite(delta_s).all(axis=(1, 2)) if cols.any() else np.zeros(len(idx), dtype=bool)

    results = []
    for nominal in coverages:
        tail = (1.0 - nominal) / 2.0
        lo, hi = _level_index(levels, tail), _level_index(levels, 1.0 - tail)
        if lo is None or hi is None:
            continue
        ok = np.isfinite(delta[:, [lo, hi], :]).all(axis=(1, 2))
        if not ok.any():
            continue
        raw_in = (y[ok] >= base[ok, lo, :]) & (y[ok] <= base[ok, hi, :])
        cal_lo, cal_hi = base[ok, lo, :] + delta[ok, lo, :], base[ok, hi, :] + delta[ok, hi, :]
        cal_in = (y[ok] >= cal_lo) & (y[ok] <= cal_hi)
        results.append(
            CoverageResult(
                nominal=nominal,
                raw=float(raw_in.mean()),
                calibrated=float(cal_in.mean()),
                width_raw=float((base[ok, hi, :] - base[ok, lo, :]).mean()),
                width_calibrated=float((cal_hi - cal_lo).mean()),
                n_origins=int(ok.sum()),
                raw_by_step=raw_in.mean(axis=0),
                calibrated_by_step=cal_in.mean(axis=0),
            )
        )

    reliability_raw = np.full(len(levels), np.nan)
    reliability_cal = np.full(len(levels), np.nan)
    for j in range(len(levels)):
        reliability_raw[j] = float((y <= base[:, j, :]).mean())
        good = np.isfinite(delta[:, j, :]).all(axis=1)
        if good.any():
            reliability_cal[j] = float((y[good] <= base[good, j, :] + delta[good, j, :]).mean())

    pit_raw = scores.take(idx).pit()
    pit_cal = np.full_like(pit_raw, np.nan)
    lv = np.asarray(levels_sel)
    for row in np.flatnonzero(usable):
        for h in range(scores.horizon):
            adjusted = np.maximum.accumulate(base_s[row, :, h] + delta_s[row, :, h])
            pit_cal[row, h] = np.interp(y[row, h], adjusted, lv)
    pit_cal_valid = pit_cal[usable]

    # raw and calibrated are scored on the same levels, so the comparison is fair
    qs_raw = (
        float(quantile_score(levels_sel, base_s, y).mean())
        if cols.any()
        else float(quantile_score(levels, base, y).mean())
    )
    qs_cal = (
        float(quantile_score(levels_sel, base_s[usable] + delta_s[usable], y[usable]).mean())
        if usable.any()
        else float("nan")
    )

    crps_raw = crps_cal = None
    if scores.samples is not None:
        crps_raw = float(crps_samples(scores.samples[idx], y).mean())
        if usable.any():
            shifted = np.stack(
                [
                    shift_samples(scores.samples[start + r], levels_sel, delta_s[r])
                    for r in map(int, np.flatnonzero(usable))
                ]
            )
            crps_cal = float(crps_samples(shifted, y[usable]).mean())

    return CalibrationReport(
        method=method,
        model_id=scores.model_id,
        n_scores=n,
        n_holdout=int(usable.sum()),
        coverages=tuple(results),
        pit_bins=pit_bins,
        pit_raw=pit_histogram(pit_raw, pit_bins),
        pit_calibrated=pit_histogram(pit_cal_valid, pit_bins)
        if usable.any()
        else np.zeros(pit_bins, dtype=np.int64),
        reliability_levels=levels,
        reliability_raw=reliability_raw,
        reliability_calibrated=reliability_cal,
        quantile_score_raw=qs_raw,
        quantile_score_calibrated=qs_cal,
        crps_raw=crps_raw,
        crps_calibrated=crps_cal,
    )
