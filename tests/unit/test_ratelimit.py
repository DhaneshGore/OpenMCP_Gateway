from __future__ import annotations

import pytest

from openmcp_gateway.errors import RateLimitedError
from openmcp_gateway.security.ratelimit import RateLimiter, TokenBucketLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_refill() -> None:
    clock = Clock()
    lim = TokenBucketLimiter(rate=2, burst=3, clock=clock)
    assert [lim.try_acquire("a") for _ in range(3)] == [0, 0, 0]
    wait = lim.try_acquire("a")
    assert wait == pytest.approx(0.5)
    clock.now += 0.5
    assert lim.try_acquire("a") == 0


def test_keys_are_independent_and_bounded() -> None:
    clock = Clock()
    lim = TokenBucketLimiter(rate=1, burst=1, clock=clock, max_keys=2)
    assert lim.try_acquire("a") == 0
    assert lim.try_acquire("b") == 0
    assert lim.try_acquire("a") > 0
    lim.try_acquire("c")  # evicts the least recently used key ("b")
    assert len(lim._buckets) == 2


def test_rate_limiter_raises_with_retry_hint() -> None:
    limiter = RateLimiter(global_limit=None, per_principal=TokenBucketLimiter(rate=1, burst=1, clock=Clock()))
    limiter.check("alice")
    with pytest.raises(RateLimitedError) as info:
        limiter.check("alice")
    assert info.value.data == {"retryAfterSeconds": 1.0}
    limiter.check("bob")


def test_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="rate"):
        TokenBucketLimiter(rate=0, burst=1)
