"""Calibration scores: what past forecasts said versus what then happened.

Calibration needs a record of how a forecaster's intervals have fared. This module builds it
by *replaying* the forecaster at past origins: at each origin ``t`` it forecasts using only
data known at ``t``, then records the outcomes at ``t + 1 ... t + horizon``.

The leakage rule specific to calibration, enforced here and again in
:func:`~tycheon.calibration.conformal.calibrate`: **every outcome used must have been
published by the forecast's ``as_of``**. An origin is only usable if its whole horizon of
outcomes is already known, so the last usable origin is ``horizon`` bars before the end of
the data.

All quantities are in log-return space relative to the origin's last close,
``log(price / last_close)``. That makes scores from different price levels comparable and
turns the conformal adjustment into a simple additive shift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from tycheon.data.asof import AVAILABLE_AT, as_utc, assert_available, to_ns
from tycheon.errors import DataValidationError, ModelError

if TYPE_CHECKING:
    from datetime import datetime

    from tycheon.models.base import ForecastDistribution, Forecaster

Floats = NDArray[np.float64]

#: Quantile levels recorded by default: enough for 50/80/90/95/98% central intervals.
DEFAULT_LEVELS: tuple[float, ...] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    0.75,
    0.9,
    0.95,
    0.975,
    0.99,
)


@dataclass(frozen=True)
class ScoreSet:
    """A forecaster's recorded record at past origins, in chronological order.

    Attributes:
        origin_times: When each forecast was made (the origin bar's ``available_at``).
        outcome_times: ``(n, horizon)`` int64 nanoseconds (UTC): when each realised bar became
            known. Used to order feedback and to prove nothing after ``as_of`` was used.
        base_logq: ``(n, levels, horizon)`` the forecaster's quantiles of ``log(P/P0)``.
        realized: ``(n, horizon)`` the realised ``log(P/P0)``.
        samples: ``(n, n_samples, horizon)`` the forecast sample paths in the same space, or
            ``None`` for a quantile-only model.
    """

    model_id: str
    horizon: int
    levels: tuple[float, ...]
    origin_times: pd.DatetimeIndex
    outcome_times: NDArray[np.int64]
    base_logq: Floats
    realized: Floats
    samples: Floats | None
    n_samples: int
    stride: int
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        n, levels, horizon = len(self.origin_times), len(self.levels), self.horizon
        if self.base_logq.shape != (n, levels, horizon):
            raise DataValidationError(f"base_logq has shape {self.base_logq.shape}")
        if self.realized.shape != (n, horizon) or self.outcome_times.shape != (n, horizon):
            raise DataValidationError("realized and outcome_times must have shape (n, horizon)")
        if self.samples is not None and (
            self.samples.ndim != 3 or self.samples.shape[0] != n or self.samples.shape[2] != horizon
        ):
            raise DataValidationError(f"samples have shape {self.samples.shape}")
        if n and not self.origin_times.is_monotonic_increasing:
            raise DataValidationError("origins must be in chronological order")

    @property
    def n(self) -> int:
        return len(self.origin_times)

    @property
    def max_outcome_time(self) -> pd.Timestamp:
        """The latest moment any outcome in this set became known."""
        if self.n == 0:
            return pd.Timestamp.min.tz_localize("UTC")
        return pd.Timestamp(int(self.outcome_times.max()), tz="UTC")

    def take(self, index: NDArray[np.intp] | slice) -> ScoreSet:
        """A sub-set of origins (kept in the same order)."""
        return ScoreSet(
            model_id=self.model_id,
            horizon=self.horizon,
            levels=self.levels,
            origin_times=self.origin_times[index],
            outcome_times=self.outcome_times[index],
            base_logq=self.base_logq[index],
            realized=self.realized[index],
            samples=None if self.samples is None else self.samples[index],
            n_samples=self.n_samples,
            stride=self.stride,
            notes=self.notes,
        )

    def split_recent(self, holdout: int) -> tuple[ScoreSet, ScoreSet]:
        """Older origins for fitting, the most recent ``holdout`` for checking.

        Fit origins whose outcomes were not yet known when the first holdout origin was made
        are *purged*: otherwise the fit would use information the holdout forecasts could not
        have had when overlapping horizons are used.
        """
        if not 0 < holdout < self.n:
            raise ValueError(f"holdout must be between 1 and {self.n - 1}")
        first_holdout = int(to_ns(self.origin_times)[self.n - holdout])
        older = np.arange(self.n - holdout)
        keep = older[self.outcome_times[older].max(axis=1) <= first_holdout]
        return self.take(keep), self.take(slice(self.n - holdout, self.n))

    def pit(self) -> Floats:
        """Probability integral transform of each realised outcome, shape ``(n, horizon)``.

        From the sample paths when present (mid-rank, so ties are split), otherwise by
        interpolating the recorded quantiles. For a calibrated forecaster these are uniform.
        """
        if self.samples is not None:
            below = (self.samples < self.realized[:, None, :]).sum(axis=1)
            equal = (self.samples == self.realized[:, None, :]).sum(axis=1)
            return np.asarray((below + 0.5 * equal) / self.samples.shape[1], dtype=np.float64)
        out = np.empty_like(self.realized)
        levels = np.asarray(self.levels)
        for i in range(self.n):
            for h in range(self.horizon):
                out[i, h] = np.interp(self.realized[i, h], self.base_logq[i, :, h], levels)
        return out


def _supported_levels(dist: ForecastDistribution, levels: tuple[float, ...]) -> tuple[float, ...]:
    keep = []
    for level in levels:
        try:
            dist.quantile(level)
        except ValueError:
            continue
        keep.append(level)
    return tuple(keep)


def collect_scores(
    forecaster: Forecaster,
    bars: pd.DataFrame,
    *,
    as_of: datetime,
    horizon: int,
    n_origins: int,
    stride: int | None = None,
    n_samples: int = 50,
    levels: tuple[float, ...] = DEFAULT_LEVELS,
    min_history: int = 60,
    max_history: int | None = None,
    seed: int = 0,
) -> ScoreSet:
    """Replay ``forecaster`` at ``n_origins`` past origins and record forecasts versus outcomes.

    Args:
        bars: One symbol's bars as known at ``as_of`` (with ``available_at``). Anything
            published after ``as_of`` raises ``LookaheadError``.
        horizon: Bars ahead. Every outcome up to ``horizon`` must already be known, which
            fixes the last usable origin at ``horizon`` bars before the end of ``bars``.
        stride: Bars between origins (default ``horizon``, so horizons do not overlap and the
            scores are close to exchangeable). A smaller stride gives more scores but they
            are dependent; the effective count is noted on the result.
        n_samples: Paths per forecast. Use the same value when calibrating real forecasts:
            quantile noise depends on it.
        max_history: Cap the history given to the forecaster (default: everything known at
            the origin), for models with a fixed context.

    The cost is ``n_origins`` forecasts: budget accordingly for a slow model.
    """
    as_of_ts = as_utc(as_of)
    assert_available(bars, as_of_ts)
    if horizon < 1 or n_origins < 1:
        raise ValueError("horizon and n_origins must be positive")
    step = horizon if stride is None else stride
    if step < 1:
        raise ValueError("stride must be at least 1")

    close = bars["close"].to_numpy(dtype=np.float64)
    available = pd.DatetimeIndex(pd.to_datetime(bars[AVAILABLE_AT], utc=True))
    available_ns = to_ns(available)
    last_origin = len(bars) - 1 - horizon
    origins = [last_origin - k * step for k in range(n_origins)]
    origins = [i for i in origins if i + 1 >= min_history][::-1]
    if not origins:
        raise DataValidationError(
            f"{len(bars)} bars are too few for horizon {horizon} with min_history {min_history}"
        )

    records: list[tuple[int, ForecastDistribution]] = []
    for k, i in enumerate(origins):
        start = 0 if max_history is None else max(0, i + 1 - max_history)
        history = bars.iloc[start : i + 1]
        dist = forecaster.predict(history, horizon, n_samples, available[i], seed=seed + k)
        records.append((i, dist))

    first = records[0][1]
    usable = _supported_levels(first, levels)
    if not usable:
        raise ModelError(f"{forecaster.model_id} supports none of the requested levels")

    n = len(records)
    base = np.empty((n, len(usable), horizon))
    realized = np.empty((n, horizon))
    outcome_times = np.empty((n, horizon), dtype=np.int64)
    samples: list[Floats] = []
    for row, (i, dist) in enumerate(records):
        last = dist.last_close
        for j, level in enumerate(usable):
            base[row, j] = np.log(dist.quantile(level) / last)
        realized[row] = np.log(close[i + 1 : i + 1 + horizon] / close[i])
        outcome_times[row] = available_ns[i + 1 : i + 1 + horizon]
        if dist.samples is not None:
            samples.append(np.log(dist.samples / last))

    notes = []
    if step < horizon:
        notes.append(
            f"overlapping horizons (stride {step} < horizon {horizon}): scores are dependent, "
            f"effective n is about {n * step / horizon:.0f}"
        )
    return ScoreSet(
        model_id=forecaster.model_id,
        horizon=horizon,
        levels=usable,
        origin_times=pd.DatetimeIndex([available[i] for i, _ in records]),
        outcome_times=outcome_times,
        base_logq=base,
        realized=realized,
        samples=np.stack(samples) if len(samples) == n else None,
        n_samples=n_samples,
        stride=step,
        notes=tuple(notes),
    )
