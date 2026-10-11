"""Optional load tests. Not run by default or on pull requests.

Run with:  pytest -m load -s tests/load

These check correctness under sustained concurrency and print throughput and
latency figures for *this* machine. They are not benchmarks to publish: the
numbers depend on hardware, Python version and the upstream used.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import pytest

from tests.conftest import fake_server_config, gateway_config
from tests.e2e.harness import run_gateway

pytestmark = [pytest.mark.load, pytest.mark.e2e]

CLIENTS = int(os.environ.get("LOAD_CLIENTS", "10"))
CALLS_PER_CLIENT = int(os.environ.get("LOAD_CALLS", "200"))
IN_FLIGHT_PER_CLIENT = int(os.environ.get("LOAD_IN_FLIGHT", "10"))


async def test_sustained_concurrent_tool_calls() -> None:
    cfg = gateway_config([fake_server_config("f", max_concurrency=128, max_queue=10_000, queue_timeout=60)])
    latencies: list[float] = []
    async with run_gateway(cfg) as gw:

        async def client_run(c: int) -> int:
            ok = 0
            sem = asyncio.Semaphore(IN_FLIGHT_PER_CLIENT)
            async with gw.client() as client:

                async def one(i: int) -> None:
                    nonlocal ok
                    async with sem:
                        start = time.perf_counter()
                        res = await client.call_tool(
                            "f__sleep", {"seconds": (i % 10) / 1000, "text": f"{c}:{i}"}
                        )
                        latencies.append(time.perf_counter() - start)
                        assert res.content[0].text == f"{c}:{i}"  # type: ignore[union-attr]
                        ok += 1

                await asyncio.gather(*(one(i) for i in range(CALLS_PER_CLIENT)))
            return ok

        started = time.perf_counter()
        results = await asyncio.gather(*(client_run(c) for c in range(CLIENTS)))
        elapsed = time.perf_counter() - started

    total = sum(results)
    assert total == CLIENTS * CALLS_PER_CLIENT
    q = statistics.quantiles(latencies, n=100)
    print(
        f"\n{total} calls, {CLIENTS} clients, {elapsed:.2f}s, {total / elapsed:.0f} calls/s, "
        f"p50={q[49] * 1000:.1f}ms p95={q[94] * 1000:.1f}ms p99={q[98] * 1000:.1f}ms"
    )
