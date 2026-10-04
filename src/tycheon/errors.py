"""Exceptions shared across Tycheon.

Everything derives from :class:`TycheonError` so a caller can catch the family,
and the point-in-time and licensing failures are distinct types because they are
not ordinary bugs: a swallowed :class:`LookaheadError` is a silently inflated
result, which is why nothing in this package catches one.
"""

from __future__ import annotations


class TycheonError(Exception):
    """Base class for every error Tycheon raises on purpose."""


class LookaheadError(TycheonError, ValueError):
    """A read or a model input would let information from after ``as_of`` through.

    Raised, never warned: a result computed with lookahead looks plausible and is
    wrong, so there is no safe way to continue.
    """


class DataValidationError(TycheonError, ValueError):
    """Market data that does not satisfy the bar schema."""


class ProviderError(TycheonError):
    """A market-data provider could not serve a request."""


class ProviderDisabledError(ProviderError):
    """A provider was used where its licence terms or this environment forbid it."""


class MissingCredentialsError(ProviderError):
    """A licensed-vendor provider was used without the customer's own key."""


class OptionalDependencyError(TycheonError, ImportError):
    """A model needs an optional extra that is not installed."""


class ModelError(TycheonError):
    """A forecaster could not produce a forecast."""
