"""Turning a bars history into the tensor-ready window Kronos consumes.

Kronos sees six channels per bar (open, high, low, close, volume, amount) and five
calendar features (minute, hour, weekday, day, month). Upstream's predictor
normalises each channel by the mean and standard deviation of the *entire* frame it
is handed, then lets the model see only the last ``max_context`` rows of it. For a
history longer than the context those two disagree: the statistics describe bars the
model never reads.

The strategy here is **recent window, normalise what the model sees**:

1. keep the most recent ``min(len(history), lookback, max_context)`` bars;
2. compute mean and standard deviation over *those* bars only (population standard
   deviation, as upstream does), normalise, and clip to ``+-clip``;
3. de-normalise the forecast with the same statistics.

Everything else matches upstream exactly: missing ``volume`` and ``amount`` become
zeros, a present ``volume`` without ``amount`` gets ``volume * mean(OHLC)``, and the
``1e-5`` epsilon is the same. This module imports neither torch nor the vendored
code, so it is cheap to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    from numpy.typing import NDArray

FEATURES: tuple[str, ...] = ("open", "high", "low", "close", "volume", "amount")
CLOSE_INDEX = FEATURES.index("close")
_PRICES = ("open", "high", "low", "close")
_EPS = 1e-5


@dataclass(frozen=True)
class ContextWindow:
    """The model-ready view of a history: normalised values, their statistics, calendar stamps."""

    normalized: NDArray[np.float32]
    mean: NDArray[np.float64]
    std: NDArray[np.float64]
    stamps: NDArray[np.float32]
    length: int
    truncated: bool

    def denormalize(self, values: NDArray[np.floating]) -> NDArray[np.float64]:
        """Map model-space values (last axis = the six channels) back to price/volume units."""
        return np.asarray(values, dtype=np.float64) * (self.std + _EPS) + self.mean


def feature_matrix(frame: pd.DataFrame) -> NDArray[np.float64]:
    """The ``(n, 6)`` channel matrix, filling volume and amount the way upstream does."""
    missing = [c for c in _PRICES if c not in frame.columns]
    if missing:
        raise DataValidationError(f"Kronos needs open, high, low, close; missing {missing}")
    out = pd.DataFrame(index=frame.index)
    for column in _PRICES:
        out[column] = frame[column].to_numpy(dtype="float64")
    if "volume" not in frame.columns:
        out["volume"] = 0.0
        out["amount"] = 0.0
    else:
        out["volume"] = frame["volume"].to_numpy(dtype="float64")
        if "amount" in frame.columns:
            out["amount"] = frame["amount"].to_numpy(dtype="float64")
        else:
            out["amount"] = out["volume"].to_numpy() * out[list(_PRICES)].mean(axis=1).to_numpy()
    matrix = out[list(FEATURES)].to_numpy(dtype="float64")
    if not np.isfinite(matrix).all():
        raise DataValidationError("Kronos input contains NaN or infinity")
    return matrix


def calendar_features(index: pd.DatetimeIndex) -> NDArray[np.float32]:
    """``(n, 5)`` wall-clock features: minute, hour, weekday, day, month.

    Read in the index own timezone. For intraday data pass a history in the
    exchange local time, because that is the clock Kronos was trained against.
    """
    return np.column_stack(
        [index.minute, index.hour, index.dayofweek, index.day, index.month]
    ).astype(np.float32)


def window_context(
    frame: pd.DataFrame,
    *,
    max_context: int,
    lookback: int | None = None,
    clip: float = 5.0,
) -> ContextWindow:
    """Build the normalised window for the most recent bars of ``frame``."""
    if max_context < 2:
        raise ValueError("max_context must be at least 2")
    if lookback is not None and lookback < 2:
        raise ValueError("lookback must be at least 2")
    wanted = max_context if lookback is None else min(lookback, max_context)
    used = min(len(frame), wanted)
    if used < 2:
        raise DataValidationError("need at least two bars of history for Kronos")

    recent = frame.iloc[-used:]
    values = feature_matrix(recent)
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    normalized = np.clip((values - mean) / (std + _EPS), -clip, clip).astype(np.float32)
    return ContextWindow(
        normalized=normalized,
        mean=mean,
        std=std,
        stamps=calendar_features(pd.DatetimeIndex(recent.index)),
        length=used,
        truncated=len(frame) > used,
    )
