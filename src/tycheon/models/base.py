"""The forecaster contract: ``Forecaster``, ``ForecastDistribution`` and ``BaseForecaster``.

Three rules from ``AGENTS.md`` are encoded here so no individual model can forget them:

* **Every forecast carries uncertainty.** A :class:`ForecastDistribution` always has
  quantiles; models that can sample also carry the sample paths. There is no
  point-forecast-only return type.
* **Every forecast says how much to trust it.** Calibration status, the model mix,
  ``as_of`` and a model-card reference travel with the numbers.
* **Every read is point-in-time.** :meth:`BaseForecaster.predict` refuses a history
  that reaches past ``as_of`` before any model code runs.

Models that only emit quantiles (TimesFM, Chronos) cannot produce joint sample
paths, and Tycheon will not fabricate them: ``samples`` is ``None`` for those, and
path-dependent risk measures (drawdown, expected shortfall over a horizon) must
refuse them rather than assume how the horizon steps are correlated.
"""

from __future__ import annotations

import math
import operator
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from tycheon.data.asof import AVAILABLE_AT, as_utc, assert_available
from tycheon.errors import DataValidationError, LookaheadError, ModelError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

CalibrationStatus = Literal["uncalibrated", "calibrated", "stale"]

#: Required on every user-facing surface (AGENTS.md).
DISCLAIMER = "For research and risk analytics. Not investment advice."

#: Levels reported when a model produces samples and the caller asks for no others.
DEFAULT_QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)

Floats = NDArray[np.float64]


@dataclass(frozen=True)
class ForecastMetadata:
    """Provenance of a forecast: what produced it, from how much data, with what settings."""

    model_id: str
    model_version: str
    context_length_used: int
    context_length_available: int
    horizon: int
    n_samples: int
    seed: int | None
    device: str
    params: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


def _frozen(array: NDArray[Any]) -> NDArray[Any]:
    out = np.array(array, dtype=np.float64, copy=True)
    out.flags.writeable = False
    return out


@dataclass(frozen=True)
class ForecastDistribution:
    """A predictive distribution for the **close** price at each of ``horizon`` future bars.

    ``quantiles`` has shape ``(len(quantile_levels), horizon)`` and is non-decreasing
    along the level axis. ``samples``, when present, has shape ``(n_samples,
    horizon)``: each row is one *joint* path, so path-dependent quantities are valid
    on it. Arrays are copied and made read-only.
    """

    index: pd.DatetimeIndex
    quantile_levels: tuple[float, ...]
    quantiles: Floats
    samples: Floats | None
    last_close: float
    as_of: pd.Timestamp
    last_observation: pd.Timestamp
    metadata: ForecastMetadata
    model_mix: Mapping[str, float]
    model_card: str
    calibration_status: CalibrationStatus = "uncalibrated"
    target: str = "close"
    extras: Mapping[str, Floats] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._check_time_axis()
        levels = np.asarray(self.quantile_levels, dtype=np.float64)
        if levels.size == 0 or not np.all(np.diff(levels) > 0) or levels[0] <= 0 or levels[-1] >= 1:
            raise DataValidationError("quantile levels must be strictly increasing within (0, 1)")

        quantiles = _frozen(self.quantiles)
        if quantiles.shape != (levels.size, self.horizon):
            raise DataValidationError(
                f"quantiles have shape {quantiles.shape}, expected {(levels.size, self.horizon)}"
            )
        if not np.isfinite(quantiles).all():
            raise ModelError("forecast quantiles contain NaN or infinity")
        if (np.diff(quantiles, axis=0) < 0).any():
            raise DataValidationError("quantiles cross; sort them before constructing")
        object.__setattr__(self, "quantiles", quantiles)

        if self.samples is not None:
            object.__setattr__(self, "samples", self._checked_samples(self.samples))
        object.__setattr__(self, "extras", {k: _frozen(v) for k, v in self.extras.items()})
        self._check_provenance()

    def _check_time_axis(self) -> None:
        if self.horizon < 1:
            raise ModelError("a forecast needs at least one step")
        if self.index.tz is None or self.as_of.tzinfo is None:
            raise DataValidationError("forecast timestamps and as_of must be timezone-aware")
        if not (self.index.is_monotonic_increasing and self.index.is_unique):
            raise DataValidationError("forecast index must be strictly increasing")
        if self.index[0] <= self.last_observation:
            raise LookaheadError("forecast index must start after the last observation")

    def _checked_samples(self, raw: Floats) -> Floats:
        samples = _frozen(raw)
        if samples.ndim != 2 or samples.shape[1] != self.horizon or samples.shape[0] < 1:
            raise DataValidationError(
                f"samples have shape {samples.shape}, expected (n, {self.horizon})"
            )
        if not np.isfinite(samples).all():
            raise ModelError("forecast samples contain NaN or infinity")
        return samples

    def _check_provenance(self) -> None:
        if not self.model_card.strip():
            raise DataValidationError("every forecast must reference a model card")
        if not self.model_mix or not math.isclose(sum(self.model_mix.values()), 1.0, abs_tol=1e-9):
            raise DataValidationError("model_mix must be non-empty and sum to 1")

    # --------------------------------------------------------------- accessors
    @property
    def horizon(self) -> int:
        return len(self.index)

    @property
    def has_paths(self) -> bool:
        """Whether joint sample paths are available (needed for path-dependent risk)."""
        return self.samples is not None

    @property
    def disclaimer(self) -> str:
        return DISCLAIMER

    def quantile(self, level: float) -> Floats:
        """The ``level`` quantile at every step.

        Exact where ``level`` is one of the stored levels; computed from the sample
        paths when they exist; interpolated between stored levels otherwise. A level
        outside what a quantile-only model produced is refused, never extrapolated.
        """
        if not 0.0 < level < 1.0:
            raise ValueError("level must be strictly between 0 and 1")
        stored = np.asarray(self.quantile_levels)
        hit = np.flatnonzero(np.isclose(stored, level))
        if hit.size:
            return np.array(self.quantiles[hit[0]])
        if self.samples is not None:
            return np.quantile(self.samples, level, axis=0)
        if level < stored[0] or level > stored[-1]:
            raise ValueError(
                f"level {level} is outside the {stored[0]}-{stored[-1]} range this model produced"
            )
        return np.array(
            [np.interp(level, stored, self.quantiles[:, step]) for step in range(self.horizon)]
        )

    @property
    def median(self) -> Floats:
        return self.quantile(0.5)

    def interval(self, coverage: float = 0.9) -> tuple[Floats, Floats]:
        """Central ``coverage`` interval, e.g. 0.9 -> the 5th and 95th percentiles."""
        if not 0.0 < coverage < 1.0:
            raise ValueError("coverage must be strictly between 0 and 1")
        tail = (1.0 - coverage) / 2.0
        return self.quantile(tail), self.quantile(1.0 - tail)

    @property
    def max_coverage(self) -> float:
        """The widest central interval this forecast can report (at most 0.9 by default).

        Sample paths support any level; a quantile-only model supports only what lies
        between its lowest and highest native levels (TimesFM: 0.1 to 0.9, so 0.8).
        """
        if self.samples is not None:
            return 0.9
        stored = self.quantile_levels
        tail = max(stored[0], 1.0 - stored[-1])
        return round(1.0 - 2.0 * tail, 10)

    def require_paths(self, purpose: str) -> Floats:
        """Return the sample paths, or refuse: quantiles alone cannot describe a path."""
        if self.samples is None:
            raise ModelError(
                f"{purpose} needs joint sample paths, which {self.metadata.model_id} does not "
                "produce (it emits marginal quantiles only)"
            )
        return self.samples

    def to_frame(self) -> pd.DataFrame:
        """Quantiles as a DataFrame indexed by forecast time, one column per level."""
        return pd.DataFrame(
            self.quantiles.T,
            index=self.index,
            columns=[f"q{level:g}" for level in self.quantile_levels],
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable record that keeps every field AGENTS.md requires."""
        return {
            "index": [ts.isoformat() for ts in self.index],
            "target": self.target,
            "quantile_levels": list(self.quantile_levels),
            "quantiles": self.quantiles.tolist(),
            "n_sample_paths": 0 if self.samples is None else int(self.samples.shape[0]),
            "last_close": self.last_close,
            "as_of": self.as_of.isoformat(),
            "last_observation": self.last_observation.isoformat(),
            "calibration_status": self.calibration_status,
            "model_mix": dict(self.model_mix),
            "model_card": self.model_card,
            "metadata": {
                "model_id": self.metadata.model_id,
                "model_version": self.metadata.model_version,
                "context_length_used": self.metadata.context_length_used,
                "context_length_available": self.metadata.context_length_available,
                "horizon": self.metadata.horizon,
                "n_samples": self.metadata.n_samples,
                "seed": self.metadata.seed,
                "device": self.metadata.device,
                "params": dict(self.metadata.params),
                "diagnostics": dict(self.metadata.diagnostics),
            },
            "disclaimer": DISCLAIMER,
        }

    def summary(self) -> str:
        """A short human-readable description, always ending with the disclaimer."""
        coverage = self.max_coverage
        lo, hi = self.interval(coverage)
        mid = self.quantile(0.5)
        paths = "sample paths" if self.has_paths else "quantiles only"
        return (
            f"{self.metadata.model_id} [{self.calibration_status}, {paths}] as_of "
            f"{self.as_of.isoformat()}: last close {self.last_close:.4g}; "
            f"step {self.horizon} median {mid[-1]:.4g}, {coverage:.0%} interval "
            f"[{lo[-1]:.4g}, {hi[-1]:.4g}]. {DISCLAIMER}"
        )


@runtime_checkable
class Forecaster(Protocol):
    """What the rest of Tycheon (routing, calibration, backtests) relies on."""

    model_id: str
    model_card: str
    supports_paths: bool

    def fit(self, history: pd.DataFrame | None = None) -> Forecaster:
        """Optional training or calibration step. Models that need none return ``self``."""
        ...

    def predict(
        self,
        history: pd.DataFrame,
        horizon: int,
        n_samples: int,
        as_of: datetime,
        *,
        seed: int | None = None,
    ) -> ForecastDistribution:
        """Forecast ``horizon`` bars after the end of ``history``, as known at ``as_of``."""
        ...


@dataclass(frozen=True)
class PreparedHistory:
    """A validated history, ready for a model."""

    frame: pd.DataFrame
    as_of: pd.Timestamp
    last_observation: pd.Timestamp
    last_close: float
    future_index: pd.DatetimeIndex


@dataclass
class RawForecast:
    """What a model returns to :class:`BaseForecaster`; it turns this into a distribution."""

    samples: Floats | None
    quantile_levels: tuple[float, ...] | None = None
    quantiles: Floats | None = None
    extras: dict[str, Floats] = field(default_factory=dict)
    model_version: str = "n/a"
    context_length_used: int = 0
    device: str = "cpu"
    params: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _infer_step(index: pd.DatetimeIndex) -> pd.offsets.BaseOffset | pd.Timedelta:
    """The step between bars: a pandas offset when one fits, else the median spacing."""
    tail = index[-min(len(index), 64) :]
    if len(tail) >= 3:
        inferred = pd.infer_freq(tail)
        if inferred is not None:
            return pd.tseries.frequencies.to_offset(inferred)
    deltas = pd.Series(tail[1:] - tail[:-1])
    median = pd.Timedelta(deltas.median())
    # Daily-ish bars that never land on a weekend are business-day bars.
    if median >= pd.Timedelta(days=1) and bool((tail.dayofweek < 5).all()):
        return pd.offsets.BDay()
    return median


def forecast_index(index: pd.DatetimeIndex, horizon: int) -> pd.DatetimeIndex:
    """Timestamps for the ``horizon`` bars after ``index``, continuing its observed cadence.

    Cadence is inferred from the history. Intraday series with overnight or weekend
    gaps are continued at their median spacing, so those labels are approximate; the
    values of a forecast never depend on them except for Kronos' calendar features.
    """
    step = _infer_step(index)
    if isinstance(step, pd.Timedelta):
        return pd.DatetimeIndex(
            [index[-1] + step * (k + 1) for k in range(horizon)], name="timestamp"
        )
    return pd.date_range(index[-1] + step, periods=horizon, freq=step, name="timestamp")


def _positive_int(value: Any, name: str) -> int:
    """``value`` as an int if it is a genuine positive integer (numpy ints allowed)."""
    try:
        number = operator.index(value)
    except TypeError:
        raise ValueError(f"{name} must be a positive integer") from None
    if isinstance(value, bool) or number < 1:
        raise ValueError(f"{name} must be a positive integer")
    return number


def prepare_history(
    history: pd.DataFrame,
    horizon: int,
    as_of: datetime,
    *,
    required: tuple[str, ...] = ("close",),
    min_length: int = 2,
) -> PreparedHistory:
    """Validate ``history`` against ``as_of`` and the model contract.

    This is where lookahead is refused. If ``history`` carries ``available_at`` it is
    compared with ``as_of``; otherwise the bar timestamps themselves must not be
    after ``as_of`` (a weaker check, because without publication times a late
    restatement cannot be detected, so prefer histories read from the store).
    """
    if not isinstance(history, pd.DataFrame):
        raise DataValidationError("history must be a pandas DataFrame")
    horizon = _positive_int(horizon, "horizon")
    as_of_ts = as_utc(as_of)

    index = history.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise DataValidationError("history must have a timezone-aware DatetimeIndex")
    if not (index.is_monotonic_increasing and index.is_unique):
        raise DataValidationError("history index must be strictly increasing")
    missing = [c for c in required if c not in history.columns]
    if missing:
        raise DataValidationError(f"history is missing columns {missing}")
    if len(history) < min_length:
        raise DataValidationError(f"history has {len(history)} bars; need at least {min_length}")

    if AVAILABLE_AT in history.columns:
        assert_available(history, as_of_ts)
    elif index.max() > as_of_ts:
        raise LookaheadError(
            f"history reaches {index.max().isoformat()}, after as_of={as_of_ts.isoformat()}"
        )

    frame = history.drop(columns=[AVAILABLE_AT], errors="ignore").astype(
        {c: "float64" for c in history.columns if c != AVAILABLE_AT}
    )
    values = frame[list(required)].to_numpy(dtype="float64")
    if not np.isfinite(values).all():
        raise DataValidationError("history contains NaN or infinity")
    if (frame["close"].to_numpy() <= 0).any():
        raise DataValidationError("history has non-positive closes")

    return PreparedHistory(
        frame=frame,
        as_of=as_of_ts,
        last_observation=pd.Timestamp(index[-1]),
        last_close=float(frame["close"].iloc[-1]),
        # Kept in the history timezone, not converted: Kronos derives wall-clock
        # calendar features from it, and for intraday data that must be exchange time.
        future_index=forecast_index(index, horizon),
    )


class BaseForecaster(ABC):
    """Template for forecasters: validation and packaging here, the model in :meth:`_forecast`."""

    model_id: str
    model_card: str
    supports_paths: bool = True
    #: Columns :meth:`_forecast` needs besides ``close``.
    required_columns: tuple[str, ...] = ("close",)
    #: Minimum bars of history the model can work with.
    min_history: int = 2

    def __init__(self, *, seed: int | None = 0) -> None:
        self.seed = seed

    def fit(self, history: pd.DataFrame | None = None) -> BaseForecaster:
        """No-op by default: baselines and zero-shot foundation models need no fitting."""
        del history
        return self

    @abstractmethod
    def _forecast(
        self, prepared: PreparedHistory, horizon: int, n_samples: int, seed: int | None
    ) -> RawForecast:
        """Produce the raw forecast for a validated history."""

    def predict(
        self,
        history: pd.DataFrame,
        horizon: int,
        n_samples: int,
        as_of: datetime,
        *,
        seed: int | None = None,
    ) -> ForecastDistribution:
        n_samples = _positive_int(n_samples, "n_samples")
        prepared = prepare_history(
            history, horizon, as_of, required=self.required_columns, min_length=self.min_history
        )
        used_seed = self.seed if seed is None else seed
        raw = self._forecast(prepared, int(horizon), int(n_samples), used_seed)
        return self._package(prepared, int(horizon), used_seed, raw)

    def _package(
        self,
        prepared: PreparedHistory,
        horizon: int,
        seed: int | None,
        raw: RawForecast,
    ) -> ForecastDistribution:
        diagnostics = dict(raw.diagnostics)
        if raw.samples is not None:
            levels = raw.quantile_levels or DEFAULT_QUANTILE_LEVELS
            quantiles = np.quantile(raw.samples, levels, axis=0)
            nonpositive = int((raw.samples <= 0).any(axis=1).sum())
            if nonpositive:
                diagnostics["paths_with_nonpositive_close"] = nonpositive
        elif raw.quantiles is not None and raw.quantile_levels is not None:
            levels = raw.quantile_levels
            quantiles = np.asarray(raw.quantiles, dtype=np.float64)
            # Marginal quantiles from a network can cross. Sorting along the level
            # axis (rearrangement) is the standard repair and keeps each marginal
            # calibrated; record that it happened rather than hide it.
            if (np.diff(quantiles, axis=0) < 0).any():
                quantiles = np.sort(quantiles, axis=0)
                diagnostics["quantile_crossing_repaired"] = True
        else:
            raise ModelError(f"{self.model_id} returned neither samples nor quantiles")

        metadata = ForecastMetadata(
            model_id=self.model_id,
            model_version=raw.model_version,
            context_length_used=raw.context_length_used,
            context_length_available=len(prepared.frame),
            horizon=horizon,
            n_samples=0 if raw.samples is None else int(raw.samples.shape[0]),
            seed=seed,
            device=raw.device,
            params=dict(raw.params),
            diagnostics=diagnostics,
        )
        return ForecastDistribution(
            index=prepared.future_index,
            quantile_levels=tuple(float(x) for x in levels),
            quantiles=quantiles,
            samples=raw.samples,
            last_close=prepared.last_close,
            as_of=prepared.as_of,
            last_observation=prepared.last_observation,
            metadata=metadata,
            model_mix={self.model_id: 1.0},
            model_card=self.model_card,
            extras=raw.extras,
        )
