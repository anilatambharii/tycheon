"""A per-organisation rate limit: a token bucket refilled continuously.

In-process only. With several API replicas each enforces its own bucket, so the effective limit is
the configured one times the replica count; a shared (Redis) bucket is future work and documented
as a gap. Clock injection keeps the tests deterministic.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_TRACKED = 50_000


class RateLimitedError(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after = retry_after


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """``allow(key, per_minute)`` takes one token or raises :class:`RateLimitedError`."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, per_minute: int) -> None:
        if per_minute <= 0:
            raise RateLimitedError(60.0)
        rate = per_minute / 60.0
        capacity = float(per_minute)
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= MAX_TRACKED:  # bound memory: drop the stalest quarter
                    stale = sorted(self._buckets, key=lambda k: self._buckets[k].updated)
                    for old in stale[: MAX_TRACKED // 4]:
                        del self._buckets[old]
                bucket = self._buckets[key] = _Bucket(capacity, now)
            bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated) * rate)
            bucket.updated = now
            if bucket.tokens < 1.0:
                raise RateLimitedError((1.0 - bucket.tokens) / rate)
            bucket.tokens -= 1.0
