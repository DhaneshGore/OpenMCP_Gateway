"""In-process token-bucket rate limiting.

Limits apply per gateway process. With N replicas the effective limit is N times
the configured value; distributed limiting is not implemented.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from openmcp_gateway.errors import RateLimitedError

_MAX_KEYS = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(
        self,
        rate: float,
        burst: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = _MAX_KEYS,
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate = rate
        self.burst = burst
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def try_acquire(self, key: str) -> float:
        """Consume one token. Returns 0 on success, else seconds until one is available."""
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.burst, updated=now)
            self._buckets[key] = bucket
            if len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(key)
            bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate)
            bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / self.rate


class RateLimiter:
    """Global and per-principal limits; either may be disabled (None)."""

    def __init__(
        self, global_limit: TokenBucketLimiter | None, per_principal: TokenBucketLimiter | None
    ) -> None:
        self._global = global_limit
        self._per_principal = per_principal

    def check(self, principal: str) -> None:
        if self._per_principal is not None:
            wait = self._per_principal.try_acquire(principal)
            if wait:
                raise RateLimitedError("rate limit exceeded", data={"retryAfterSeconds": round(wait, 3)})
        if self._global is not None:
            wait = self._global.try_acquire("*")
            if wait:
                raise RateLimitedError(
                    "gateway rate limit exceeded", data={"retryAfterSeconds": round(wait, 3)}
                )
