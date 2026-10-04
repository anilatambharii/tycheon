"""Market data: provider protocol, the as-of store, and synthetic sample data.

Every read in this package is point-in-time: it takes an ``as_of`` and refuses
anything published after it. See :mod:`tycheon.data.asof` for the two-clock model.
"""

from tycheon.data.asof import AVAILABLE_AT, as_utc, assert_available, filter_as_of
from tycheon.data.providers.base import MarketDataProvider, ProviderBase
from tycheon.data.sample import SAMPLE_DATASETS, SampleProvider, load_sample
from tycheon.data.store import AsOfStore

__all__ = [
    "AVAILABLE_AT",
    "SAMPLE_DATASETS",
    "AsOfStore",
    "MarketDataProvider",
    "ProviderBase",
    "SampleProvider",
    "as_utc",
    "assert_available",
    "filter_as_of",
    "load_sample",
]
