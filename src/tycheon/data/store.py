"""An append-only, bi-temporal store of bars and corporate actions (Parquet + DuckDB).

Nothing is ever updated in place. A restatement is a *new row* for the same
``timestamp`` with a later ``available_at``, so history keeps every version and the
store can answer the question that matters: **what did we know at ``as_of``?**

Layout under the root::

    bars/symbol=<SYM>/part-<ns>-<id>.parquet
    actions/symbol=<SYM>/part-<ns>-<id>.parquet

Every read takes a required ``as_of``. Rows with ``available_at`` after it do not
exist as far as the caller is concerned, and a request whose ``end`` is after
``as_of`` raises :class:`~tycheon.errors.LookaheadError` rather than being quietly
shortened, because a window that reaches into the future is a bug in the caller.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from tycheon.data import actions as actions_mod
from tycheon.data.asof import AVAILABLE_AT, as_utc, assert_available, require_not_future
from tycheon.data.schema import (
    BAR_COLUMNS,
    TIMESTAMP,
    normalize_bars,
    validate_bars,
    validate_symbol,
)
from tycheon.errors import DataValidationError

if TYPE_CHECKING:
    from datetime import datetime

    import pandas as pd

    from tycheon.data.providers.base import MarketDataProvider

_BARS = "bars"
_ACTIONS = "actions"
_QUOTE = chr(39)


def _quote(path: str) -> str:
    """SQL string literal for a filesystem path. Only ever fed paths this module built."""
    return _QUOTE + path.replace(_QUOTE, _QUOTE * 2) + _QUOTE


class AsOfStore:
    """Parquet-backed store whose every read is point-in-time."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------ paths
    def _dir(self, kind: str, symbol: str) -> Path:
        validate_symbol(symbol)
        path = (self.root / kind / f"symbol={symbol}").resolve()
        if self.root not in path.parents:
            raise DataValidationError(f"{symbol!r} resolves outside the store root")
        return path

    def _write(self, kind: str, symbol: str, frame: pd.DataFrame) -> Path:
        directory = self._dir(kind, symbol)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"part-{time.time_ns()}-{uuid.uuid4().hex[:8]}.parquet"
        frame.to_parquet(target, index=True)
        return target

    def symbols(self) -> list[str]:
        """Symbols that have bars in the store."""
        base = self.root / _BARS
        if not base.is_dir():
            return []
        return sorted(p.name.removeprefix("symbol=") for p in base.glob("symbol=*") if p.is_dir())

    # ----------------------------------------------------------------- writes
    def put_bars(self, symbol: str, bars: pd.DataFrame) -> Path:
        """Append bars. ``available_at`` is required; repeated timestamps are revisions."""
        frame = normalize_bars(bars)
        validate_bars(frame, allow_revisions=True)
        return self._write(_BARS, symbol, frame)

    def put_actions(self, symbol: str, actions: pd.DataFrame) -> Path:
        """Append corporate actions (see :mod:`tycheon.data.actions`)."""
        return self._write(_ACTIONS, symbol, actions_mod.normalize_actions(actions))

    def ingest(
        self,
        provider: MarketDataProvider,
        symbol: str,
        *,
        start: datetime | None,
        end: datetime | None,
        as_of: datetime,
        frequency: str = "1D",
    ) -> int:
        """Pull bars and actions from ``provider`` into the store; return the bar count.

        The provider guard is not trusted blindly: the result is checked against
        ``as_of`` again here, because a third-party provider that does not derive
        from :class:`~tycheon.data.providers.base.ProviderBase` has no guard.
        """
        as_of_ts = as_utc(as_of)
        bars = provider.fetch_bars(symbol, start=start, end=end, as_of=as_of, frequency=frequency)
        assert_available(bars, as_of_ts)
        self.put_bars(symbol, bars)
        acts = provider.fetch_corporate_actions(symbol, as_of=as_of)
        if not acts.empty:
            assert_available(acts, as_of_ts)
            self.put_actions(symbol, acts)
        return len(bars)

    # ------------------------------------------------------------------ reads
    def _query(self, kind: str, symbol: str, sql: str, params: list[Any]) -> pd.DataFrame:
        directory = self._dir(kind, symbol)
        if not directory.is_dir() or not any(directory.glob("*.parquet")):
            raise DataValidationError(f"no {kind} stored for symbol {symbol!r}")
        glob = _quote((directory / "*.parquet").as_posix())
        con = duckdb.connect(":memory:")
        try:
            con.execute("SET TimeZone='UTC'")
            source = f"read_parquet({glob}, union_by_name=true)"
            result = con.execute(sql.replace("{src}", source), params)
            frame: pd.DataFrame = result.to_arrow_table().to_pandas()
            return frame
        finally:
            con.close()

    def bars(
        self,
        symbol: str,
        *,
        as_of: datetime,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Bars for ``symbol`` as known at ``as_of``, latest revision of each bar.

        ``end``, if given, must not be after ``as_of``.
        """
        as_of_ts = as_utc(as_of)
        start_ts = as_utc(start, "start") if start is not None else None
        end_ts = as_utc(end, "end") if end is not None else None
        require_not_future(end_ts, as_of_ts)

        clauses = [f"{AVAILABLE_AT} <= CAST(? AS TIMESTAMPTZ)"]
        params: list[Any] = [as_of_ts.isoformat()]
        if start_ts is not None:
            clauses.append(f"{TIMESTAMP} >= CAST(? AS TIMESTAMPTZ)")
            params.append(start_ts.isoformat())
        if end_ts is not None:
            clauses.append(f"{TIMESTAMP} <= CAST(? AS TIMESTAMPTZ)")
            params.append(end_ts.isoformat())

        # Only module constants and the clause list are interpolated; every value
        # (timestamps) is a bound parameter, never part of the SQL text.
        sql = (
            "SELECT * FROM {src} WHERE "  # noqa: S608
            + " AND ".join(clauses)
            + f" QUALIFY row_number() OVER (PARTITION BY {TIMESTAMP}"
            + f" ORDER BY {AVAILABLE_AT} DESC) = 1 ORDER BY {TIMESTAMP}"
        )
        frame = self._query(_BARS, symbol, sql, params).set_index(TIMESTAMP)
        present = [c for c in (*BAR_COLUMNS, AVAILABLE_AT) if c in frame.columns]
        frame = frame[present]
        assert_available(frame, as_of_ts)
        return frame

    def actions(self, symbol: str, *, as_of: datetime) -> pd.DataFrame:
        """Corporate actions announced by ``as_of`` (an empty frame if none are stored)."""
        as_of_ts = as_utc(as_of)
        directory = self._dir(_ACTIONS, symbol)
        if not directory.is_dir() or not any(directory.glob("*.parquet")):
            return actions_mod.empty_actions()
        sql = (
            f"SELECT * FROM {{src}} WHERE {AVAILABLE_AT} <= CAST(? AS TIMESTAMPTZ) "  # noqa: S608
            f"ORDER BY {actions_mod.EX_DATE}"
        )
        frame = self._query(_ACTIONS, symbol, sql, [as_of_ts.isoformat()])
        frame = frame.set_index(actions_mod.EX_DATE)
        assert_available(frame, as_of_ts)
        return frame

    def adjusted_bars(
        self,
        symbol: str,
        *,
        as_of: datetime,
        start: datetime | None = None,
        end: datetime | None = None,
        adjust_dividends: bool = False,
    ) -> pd.DataFrame:
        """Bars adjusted only for the corporate actions known and effective at ``as_of``."""
        as_of_ts = as_utc(as_of)
        raw = self.bars(symbol, as_of=as_of, start=start, end=end)
        return actions_mod.adjust_bars(
            raw, self.actions(symbol, as_of=as_of), as_of_ts, adjust_dividends=adjust_dividends
        )
