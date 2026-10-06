"""The trusted context a service call runs in: who, as of when, over which data.

Two rules this module exists to enforce:

* ``as_of`` is **never an argument a model or an HTTP client can supply**. The harness binds it
  (``bind``) around each call, and the service functions read it (``current``). A tool whose
  input carried its own ``as_of`` would let a model ask for the future.
* Reading it without a binding is an error, not a default to "now". Fail closed.

The context is a :class:`contextvars.ContextVar`, so it follows ``await`` and
``asyncio.to_thread`` and cannot leak between concurrent calls.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tycheon.data.asof import as_utc
from tycheon.data.providers.file import FileProvider
from tycheon.data.sample import SAMPLE_DATASETS, load_sample
from tycheon.errors import TycheonError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    import pandas as pd

    from tycheon.services.fundamentals import FundamentalsStore
    from tycheon.services.news import NewsStore


class ServiceError(TycheonError):
    """A service call that cannot be answered (unknown symbol, no data, no context)."""


@dataclass(frozen=True)
class DataSource:
    """Where bars, news and fundamentals come from.

    ``sample`` is the bundled synthetic data. ``files`` reads bars the operator licensed
    themselves (``path``): Tycheon never ships or redistributes market data.
    """

    kind: Literal["sample", "files"] = "sample"
    path: Path | None = None
    news: NewsStore | None = None
    fundamentals: FundamentalsStore | None = None

    def __post_init__(self) -> None:
        if self.kind == "files" and self.path is None:
            raise ServiceError("a files data source needs a path")

    def bars(self, symbol: str, as_of: datetime) -> pd.DataFrame:
        """Bars for ``symbol`` as known at ``as_of`` (nothing published later is returned)."""
        when = as_utc(as_of)
        if self.kind == "sample":
            if symbol not in SAMPLE_DATASETS:
                raise ServiceError(f"unknown symbol; the sample data has {sorted(SAMPLE_DATASETS)}")
            return load_sample(symbol, as_of=when)
        assert self.path is not None  # noqa: S101 - checked in __post_init__
        return FileProvider(Path(self.path)).fetch_bars(
            symbol, start=None, end=None, as_of=when, frequency="1D"
        )


@dataclass(frozen=True)
class ToolContext:
    """Trusted facts for one call. Built by the harness, never from model or client input."""

    as_of: datetime
    tenant_id: str
    data: DataSource

    def __post_init__(self) -> None:
        as_utc(self.as_of)  # refuses a naive timestamp


_current: ContextVar[ToolContext | None] = ContextVar("tycheon_tool_context", default=None)


def current() -> ToolContext:
    """The context bound by the harness for this call; raises if there is none."""
    ctx = _current.get()
    if ctx is None:
        raise ServiceError("no trusted context is bound: refusing to run without an as_of")
    return ctx


@contextlib.contextmanager
def bind(ctx: ToolContext) -> Iterator[ToolContext]:
    """Run a block with ``ctx`` as the trusted context (restored on exit, even on error)."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
