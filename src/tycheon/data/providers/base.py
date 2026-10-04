"""The provider protocol and the base class that enforces ``as_of`` for every provider.

Tycheon does not sell market data. Providers are pluggable and *the customer
brings their own licence*; Tycheon never redistributes licensed exchange data.
The protocol is therefore small, and the base class does the one thing that must
not be left to each adapter's author: refusing the future.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

from tycheon.data import actions as actions_mod
from tycheon.data.asof import as_utc, assert_available, filter_as_of, require_not_future
from tycheon.data.schema import normalize_bars, validate_bars, validate_symbol
from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    from datetime import datetime


@runtime_checkable
class MarketDataProvider(Protocol):
    """Anything that can serve point-in-time OHLCV bars and corporate actions."""

    name: str
    license_terms: str
    commercial_use: bool

    def fetch_bars(
        self,
        symbol: str,
        *,
        start: datetime | None,
        end: datetime | None,
        as_of: datetime,
        frequency: str = "1D",
    ) -> pd.DataFrame:
        """Bars for ``symbol`` with ``start <= timestamp <= end``, known at ``as_of``."""
        ...

    def fetch_corporate_actions(self, symbol: str, *, as_of: datetime) -> pd.DataFrame:
        """Splits and dividends for ``symbol`` announced by ``as_of``."""
        ...


def bar_duration_for(frequency: str) -> pd.Timedelta:
    """Length of one bar for a fixed-length frequency such as ``1D``, ``1h``, ``5min``."""
    try:
        duration = pd.Timedelta(frequency)
    except ValueError as exc:
        raise DataValidationError(
            f"cannot use frequency {frequency!r}: give a fixed-length one like '1D', '1h', '5min'"
        ) from exc
    if duration <= pd.Timedelta(0):
        raise DataValidationError(f"frequency {frequency!r} must be positive")
    return duration


class ProviderBase(ABC):
    """Base class for providers: subclasses load raw data, this class guards it.

    Subclasses implement :meth:`_load_bars` (and optionally :meth:`_load_actions`)
    returning whatever the source gives them. :meth:`fetch_bars` then normalises,
    windows, filters to what was known at ``as_of`` and re-checks the result, so a
    new adapter cannot forget the guard.
    """

    name: str = "provider"
    license_terms: str = "Customer-supplied licence; Tycheon does not redistribute this data."
    commercial_use: bool = True

    #: Timezone used to read naive timestamps from the source.
    source_tz: str = "UTC"
    #: Delay between a bar closing and it being knowable, on top of its duration.
    availability_lag: pd.Timedelta = pd.Timedelta(0)

    @abstractmethod
    def _load_bars(self, symbol: str, frequency: str) -> pd.DataFrame:
        """Raw bars, indexed by bar-open time. May be naive, unsorted, or lack ``available_at``."""

    def _load_actions(self, symbol: str) -> pd.DataFrame:
        """Raw corporate actions; the default is none."""
        del symbol
        return actions_mod.empty_actions()

    def fetch_bars(
        self,
        symbol: str,
        *,
        start: datetime | None,
        end: datetime | None,
        as_of: datetime,
        frequency: str = "1D",
    ) -> pd.DataFrame:
        validate_symbol(symbol)
        as_of_ts = as_utc(as_of)
        start_ts = as_utc(start, "start") if start is not None else None
        end_ts = as_utc(end, "end") if end is not None else None
        require_not_future(end_ts, as_of_ts)

        bars = normalize_bars(
            self._load_bars(symbol, frequency),
            tz=self.source_tz,
            bar_duration=bar_duration_for(frequency),
            availability_lag=self.availability_lag,
        )
        mask = pd.Series(True, index=bars.index)
        if start_ts is not None:
            mask &= bars.index >= start_ts
        if end_ts is not None:
            mask &= bars.index <= end_ts
        known = filter_as_of(bars.loc[mask.to_numpy()], as_of_ts)
        validate_bars(known)
        assert_available(known, as_of_ts)
        return known

    def fetch_corporate_actions(self, symbol: str, *, as_of: datetime) -> pd.DataFrame:
        validate_symbol(symbol)
        as_of_ts = as_utc(as_of)
        raw = self._load_actions(symbol)
        if raw.empty:
            return actions_mod.empty_actions()
        normalised = actions_mod.normalize_actions(raw, tz=self.source_tz)
        return normalised.loc[pd.to_datetime(normalised["available_at"], utc=True) <= as_of_ts]
