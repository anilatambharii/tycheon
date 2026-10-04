"""Point-in-time primitives.

Two clocks matter for every observation. ``timestamp`` is when the bar is *about*;
``available_at`` is when it could first have been *known*. A daily bar for
2024-01-02 is about 2024-01-02 but cannot be known before that day's close, so a
forecast made at noon on the 2nd must not see it. Every guard here compares
``available_at`` with ``as_of``, never ``timestamp``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from tycheon.errors import DataValidationError, LookaheadError

if TYPE_CHECKING:
    from datetime import datetime

AVAILABLE_AT = "available_at"


def as_utc(value: datetime | pd.Timestamp | str, name: str = "as_of") -> pd.Timestamp:
    """Return ``value`` as a UTC timestamp, refusing anything timezone-naive.

    A naive ``as_of`` is ambiguous by up to a day across timezones, which is
    exactly the size of the error that turns an honest backtest into a leaky one.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        raise DataValidationError(
            f"{name} must be timezone-aware (got naive {ts!s}); pass e.g. tz='UTC'"
        )
    return ts.tz_convert("UTC")


def require_not_future(end: pd.Timestamp | None, as_of: pd.Timestamp, what: str = "end") -> None:
    """Refuse a request whose window reaches past ``as_of``.

    Asking for the future is a bug in the caller, not a request for less data, so
    it raises instead of quietly truncating.
    """
    if end is not None and end > as_of:
        raise LookaheadError(f"{what}={end.isoformat()} is after as_of={as_of.isoformat()}")


def assert_available(frame: pd.DataFrame, as_of: pd.Timestamp) -> None:
    """Raise unless every row of ``frame`` was knowable at ``as_of``."""
    if AVAILABLE_AT not in frame.columns:
        raise DataValidationError(f"frame has no {AVAILABLE_AT!r} column to check against as_of")
    if frame.empty:
        return
    latest = pd.DatetimeIndex(pd.to_datetime(frame[AVAILABLE_AT], utc=True)).max()
    if latest > as_of:
        raise LookaheadError(
            f"{AVAILABLE_AT} reaches {latest.isoformat()}, after as_of={as_of.isoformat()}"
        )


def filter_as_of(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Keep rows known at ``as_of``; where a bar was revised, keep the latest version known."""
    if AVAILABLE_AT not in frame.columns:
        raise DataValidationError(f"frame has no {AVAILABLE_AT!r} column to filter on")
    known = frame.loc[pd.to_datetime(frame[AVAILABLE_AT], utc=True) <= as_of]
    known = known.sort_values(AVAILABLE_AT, kind="stable")
    return known.loc[~known.index.duplicated(keep="last")].sort_index()
