"""The seam for licensed market-data vendors, using the customer's own credentials.

Tycheon ships no vendor adapters and no vendor data. A customer who holds a licence
with a vendor subclasses :class:`LicensedVendorProvider`, implements one method,
and passes their own API key. The base class takes care of the parts that must not
vary per vendor: reading the key from the environment, never printing it, and (via
:class:`~tycheon.data.providers.base.ProviderBase`) refusing data after ``as_of``.

.. code-block:: python

    class AcmeVendor(LicensedVendorProvider):
        name = "acme"

        def _request_bars(
            self, symbol, frequency, *, api_key
        ): ...  # call the vendor, return a frame indexed by bar-open time
"""

from __future__ import annotations

import os
from abc import abstractmethod
from typing import TYPE_CHECKING

from tycheon.data.providers.base import ProviderBase
from tycheon.data.schema import NO_LAG
from tycheon.errors import MissingCredentialsError, ProviderError

if TYPE_CHECKING:
    import pandas as pd


class LicensedVendorProvider(ProviderBase):
    """Base class for vendor adapters. Subclasses implement :meth:`_request_bars`."""

    name = "licensed-vendor"
    license_terms = (
        "Governed by the customer's own agreement with the vendor. "
        "Tycheon does not redistribute vendor data."
    )
    #: Environment variable holding the key when none is passed explicitly.
    env_var = "TYCHEON_DATA_API_KEY"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        source_tz: str = "UTC",
        availability_lag: pd.Timedelta = NO_LAG,
    ) -> None:
        raw = api_key if api_key is not None else os.environ.get(self.env_var, "")
        self._api_key = raw.strip()
        self.source_tz = source_tz
        self.availability_lag = availability_lag

    @property
    def has_credentials(self) -> bool:
        """Whether a key is configured. Never reveals the key itself."""
        return bool(self._api_key)

    def __repr__(self) -> str:
        state = "set" if self._api_key else "missing"
        return f"{type(self).__name__}(api_key=<{state}>)"

    def _require_key(self) -> str:
        if not self._api_key:
            raise MissingCredentialsError(
                f"{self.name}: no API key. Pass api_key=... or set {self.env_var}. "
                "Tycheon does not supply market data; bring your own licence."
            )
        return self._api_key

    def _load_bars(self, symbol: str, frequency: str) -> pd.DataFrame:
        return self._request_bars(symbol, frequency, api_key=self._require_key())

    @abstractmethod
    def _request_bars(self, symbol: str, frequency: str, *, api_key: str) -> pd.DataFrame:
        """Fetch raw bars from the vendor, indexed by bar-open time."""


class LicensedVendorStub(LicensedVendorProvider):
    """A placeholder that shows the shape of an adapter and refuses to pretend."""

    name = "licensed-vendor-stub"

    def _request_bars(self, symbol: str, frequency: str, *, api_key: str) -> pd.DataFrame:
        del symbol, frequency, api_key
        raise ProviderError(
            "this is a stub: subclass LicensedVendorProvider and implement _request_bars "
            "for the vendor you hold a licence with"
        )
