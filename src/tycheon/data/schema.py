"""The bar schema every provider, store and model agrees on.

A *bars frame* is a :class:`pandas.DataFrame` for one symbol:

* index: a sorted ``DatetimeIndex`` named ``timestamp``, timezone-aware, giving the
  bar's **open** time;
* ``open``, ``high``, ``low``, ``close``: positive floats, no NaN;
* ``volume`` and ``amount``: optional floats (``amount`` is traded value);
* ``available_at``: when the bar could first have been known (see :mod:`.asof`).

Within a store the same ``timestamp`` may appear more than once with different
``available_at`` values: that is a restatement, and the as-of query picks the
version that was current at the time.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from tycheon.data.asof import AVAILABLE_AT
from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable

TIMESTAMP = "timestamp"
NO_LAG = pd.Timedelta(0)
PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")
VOLUME_COLUMNS: tuple[str, ...] = ("volume", "amount")
BAR_COLUMNS: tuple[str, ...] = (*PRICE_COLUMNS, *VOLUME_COLUMNS)


def normalize_bars(
    frame: pd.DataFrame,
    *,
    tz: str = "UTC",
    bar_duration: pd.Timedelta | None = None,
    availability_lag: pd.Timedelta = NO_LAG,
) -> pd.DataFrame:
    """Return ``frame`` in canonical form: UTC index, float columns, ``available_at`` set.

    Naive timestamps are interpreted in ``tz``. If the frame has no
    ``available_at`` it is derived as ``timestamp + bar_duration + availability_lag``,
    the *conservative* reading: a bar is knowable no earlier than its own close.
    Pass ``bar_duration`` for any frame that lacks real publication times.
    """
    out = frame.copy()
    index = pd.DatetimeIndex(out.index)
    if index.tz is None:
        index = index.tz_localize(tz)
    out.index = index.tz_convert("UTC").rename(TIMESTAMP)

    for col in BAR_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="raise").astype("float64")

    if AVAILABLE_AT in out.columns:
        out[AVAILABLE_AT] = pd.to_datetime(out[AVAILABLE_AT], utc=True)
    else:
        if bar_duration is None:
            raise DataValidationError(
                f"frame has no {AVAILABLE_AT!r} column and no bar_duration was given to derive it"
            )
        out[AVAILABLE_AT] = out.index + bar_duration + availability_lag

    ordered = [c for c in (*BAR_COLUMNS, AVAILABLE_AT) if c in out.columns]
    extra = [c for c in out.columns if c not in ordered]
    return out[[*ordered, *extra]].sort_index(kind="stable")


def validate_bars(
    frame: pd.DataFrame,
    *,
    require_available_at: bool = True,
    allow_revisions: bool = False,
    columns: Iterable[str] = PRICE_COLUMNS,
) -> None:
    """Raise :class:`DataValidationError` unless ``frame`` is a well-formed bars frame."""
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise DataValidationError("bars must be indexed by a DatetimeIndex")
    if frame.index.tz is None:
        raise DataValidationError("bar timestamps must be timezone-aware")
    if not frame.index.is_monotonic_increasing:
        raise DataValidationError("bar timestamps must be sorted ascending")

    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise DataValidationError(f"bars are missing required columns {missing}")
    if require_available_at and AVAILABLE_AT not in frame.columns:
        raise DataValidationError(f"bars are missing {AVAILABLE_AT!r}")

    if not allow_revisions and frame.index.has_duplicates:
        raise DataValidationError("duplicate bar timestamps (revisions are only valid in a store)")

    present = [c for c in BAR_COLUMNS if c in frame.columns]
    values = frame[present].to_numpy(dtype="float64")
    if np.isnan(values).any():
        bad = [c for c in present if frame[c].isna().any()]
        raise DataValidationError(f"NaN in columns {bad}; Kronos and the baselines reject NaN")
    if (frame[list(PRICE_COLUMNS)].to_numpy(dtype="float64") <= 0).any():
        raise DataValidationError("non-positive prices; log-return models need positive prices")
    if (frame["high"] < frame["low"]).any():
        raise DataValidationError("bars with high < low")

    if AVAILABLE_AT in frame.columns:
        available = pd.DatetimeIndex(pd.to_datetime(frame[AVAILABLE_AT], utc=True))
        if (available < frame.index).any():
            raise DataValidationError(
                f"{AVAILABLE_AT} before the bar's own timestamp: information from the future"
            )


def infer_bar_duration(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Median spacing of a bar index, used where no explicit frequency is known."""
    if len(index) < 2:
        raise DataValidationError("need at least two bars to infer a bar duration")
    deltas = pd.Series(index[1:] - index[:-1])
    return pd.Timedelta(deltas.median())


_SYMBOL = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9._\-=^]{0,31}$")


def validate_symbol(symbol: str) -> str:
    """Return ``symbol`` if it is safe to use as a key and as a path component.

    Symbols become directory names in the store and file names in the file
    provider, so anything that could escape a root directory (``..``, separators,
    drive letters, NUL) is refused rather than sanitised.
    """
    try:
        matched = _SYMBOL.fullmatch(symbol)
    except TypeError:
        matched = None
    if matched is None:
        raise DataValidationError(
            f"invalid symbol {symbol!r}: use 1-32 chars of letters, digits and . _ - = ^"
        )
    return symbol
