"""API-key authentication: a key maps to exactly one tenant, and the tenant comes from the key.

The tenant is never read from the request (a path, a header or a body field), so a caller cannot
name another tenant. Keys are compared in constant time against every configured key (no early
exit), are never logged, and must be long enough not to be guessable. Where the keys come from
(an environment variable in the CLI) is the caller's concern: nothing here stores a secret.
"""

from __future__ import annotations

import hmac
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

MIN_KEY_LENGTH = 16
_TENANT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ApiKeyError(ValueError):
    """The configured keys are unusable (malformed, too short, or ambiguous)."""


class ApiKeys:
    """A set of API keys, each bound to one tenant."""

    def __init__(self, keys: Mapping[str, str]) -> None:
        for key, tenant in keys.items():
            if len(key) < MIN_KEY_LENGTH:
                raise ApiKeyError(f"API keys must be at least {MIN_KEY_LENGTH} characters")
            if not _TENANT.fullmatch(tenant):
                raise ApiKeyError("a tenant id may only contain letters, digits, '.', '_' and '-'")
        self._keys = dict(keys)

    @classmethod
    def parse(cls, value: str | None) -> ApiKeys:
        """Parse ``{"<key>": "<tenant>"}`` JSON, or ``tenant:key`` pairs separated by commas."""
        if not value or not value.strip():
            return cls({})
        text = value.strip()
        if text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ApiKeyError("TYCHEON_API_KEYS is not valid JSON") from exc
            if not isinstance(raw, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
            ):
                raise ApiKeyError("TYCHEON_API_KEYS must map key strings to tenant strings")
            return cls(raw)
        mapping: dict[str, str] = {}
        for pair in text.split(","):
            tenant, sep, key = pair.strip().partition(":")
            if not sep or not tenant or not key:
                raise ApiKeyError("expected tenant:key pairs separated by commas")
            if key in mapping:
                raise ApiKeyError("the same API key is listed twice")
            mapping[key] = tenant
        return cls(mapping)

    def __len__(self) -> int:
        return len(self._keys)

    def tenant_for(self, presented: str | None) -> str | None:
        """The tenant a presented key belongs to, or ``None``. Constant time over all keys."""
        if not presented:
            return None
        match: str | None = None
        for key, tenant in self._keys.items():
            if hmac.compare_digest(presented.encode(), key.encode()):
                match = tenant
        return match


def extract_key(x_api_key: str | None, authorization: str | None) -> str | None:
    """The key from ``X-API-Key``, or from ``Authorization: Bearer <key>``."""
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None
