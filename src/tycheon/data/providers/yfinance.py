"""Yahoo Finance via ``yfinance``: **local development and examples only**.

This provider exists so a contributor can try Tycheon on a real ticker in thirty
seconds. It is deliberately hard to use by accident:

* Yahoo terms restrict its data to personal, non-commercial use. Tycheon must
  never ship or serve it commercially, and the cloud product never enables it.
* It refuses to construct unless ``TYCHEON_ALLOW_YFINANCE=true``, and refuses
  outright when ``TYCHEON_ENV`` names a hosted environment, whatever that flag says.
* It emits :class:`NonCommercialDataWarning` every time it is constructed.
* ``yfinance`` is not a dependency of any extra and is not in the lockfile. Install
  it by hand (``pip install yfinance``) if you want this provider.

For anything real, bring a licensed vendor through
:class:`~tycheon.data.providers.licensed.LicensedVendorProvider`.
"""

from __future__ import annotations

import importlib
import os
import warnings
from typing import Any

import pandas as pd

from tycheon.data import actions as actions_mod
from tycheon.data.providers.base import ProviderBase
from tycheon.data.schema import validate_symbol
from tycheon.errors import OptionalDependencyError, ProviderDisabledError

_HOSTED_ENVS = frozenset({"staging", "prod", "production", "cloud"})
_INTERVALS = {"1D": "1d", "1h": "1h", "30min": "30m", "15min": "15m", "5min": "5m", "1min": "1m"}


class NonCommercialDataWarning(UserWarning):
    """Emitted as a warning whenever the yfinance provider is constructed."""


class YFinanceProvider(ProviderBase):
    """Development-only Yahoo Finance provider. See the module docstring."""

    name = "yfinance (DEV ONLY, non-commercial)"
    license_terms = (
        "Yahoo Finance terms: personal, non-commercial use only. "
        "Local development and examples; never the cloud product."
    )
    commercial_use = False

    def __init__(self) -> None:
        env = os.environ.get("TYCHEON_ENV", "local").strip().lower()
        if env in _HOSTED_ENVS:
            raise ProviderDisabledError(
                f"the yfinance provider is never available when TYCHEON_ENV={env!r}"
            )
        if os.environ.get("TYCHEON_ALLOW_YFINANCE", "false").strip().lower() != "true":
            raise ProviderDisabledError(
                "the yfinance provider is development-only; set TYCHEON_ALLOW_YFINANCE=true "
                "to use it locally"
            )
        warnings.warn(
            "Using yfinance: Yahoo Finance data is for personal, non-commercial use only. "
            "Do not use it in a commercial product or redistribute it.",
            NonCommercialDataWarning,
            stacklevel=2,
        )

    @staticmethod
    def _module() -> Any:
        try:
            return importlib.import_module("yfinance")
        except ImportError as exc:
            raise OptionalDependencyError(
                "yfinance is not installed. It is deliberately not a Tycheon dependency; "
                "run `pip install yfinance` for local development."
            ) from exc

    def _load_bars(self, symbol: str, frequency: str) -> pd.DataFrame:
        validate_symbol(symbol)
        interval = _INTERVALS.get(frequency)
        if interval is None:
            raise ProviderDisabledError(
                f"frequency {frequency!r} is not supported here; use one of {sorted(_INTERVALS)}"
            )
        frame: pd.DataFrame = (
            self._module()
            .Ticker(symbol)
            .history(period="max", interval=interval, auto_adjust=False, actions=False)
        )
        frame.columns = [str(c).lower() for c in frame.columns]
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in frame.columns]
        return frame[keep]

    def _load_actions(self, symbol: str) -> pd.DataFrame:
        validate_symbol(symbol)
        raw: Any = self._module().Ticker(symbol).actions
        if raw is None or raw.empty:
            return actions_mod.empty_actions()
        rows: list[pd.DataFrame] = []
        for column, kind in (("Dividends", "dividend"), ("Stock Splits", "split")):
            if column in raw.columns:
                values = raw[column]
                values = values[values > 0]
                rows.append(pd.DataFrame({"kind": kind, "value": values}, index=values.index))
        if not rows:
            return actions_mod.empty_actions()
        # Yahoo gives no announcement time; the ex-date is the latest it could have
        # been known, so normalize_actions uses it as available_at.
        return pd.concat(rows).sort_index()
