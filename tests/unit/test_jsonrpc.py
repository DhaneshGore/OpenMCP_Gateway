"""Unit tests for the id-correlated multiplexer, independent of any process."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest

from openmcp_gateway.errors import (
    OverloadedError,
    UpstreamClosedError,
    UpstreamRpcError,
    UpstreamTimeoutError,
)
from openmcp_gateway.upstream.jsonrpc import JsonRpcMultiplexer


class Wire:
    """Captures outbound messages; tests reply via mux.handle_message."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.event = asyncio.Event()

    async def send(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)
        self.event.set()

    def requests(self) -> list[dict[str, Any]]:
        return [m for m in self.sent if "id" in m and "method" in m]

    async def wait_for(self, n: int) -> None:
        while len(self.requests()) < n:
            self.event.clear()
            await self.event.wait()


def make(**kw: Any) -> tuple[JsonRpcMultiplexer, Wire]:
    wire = Wire()
    return JsonRpcMultiplexer(send=wire.send, **kw), wire


async def test_out_of_order_responses_resolve_matching_callers() -> None:
    mux, wire = make(max_pending=200)
    n = 100
    tasks = [asyncio.create_task(mux.request("echo", {"i": i}, timeout=5)) for i in range(n)]
    await wire.wait_for(n)
    reqs = wire.requests()
    random.Random(1234).shuffle(reqs)
    for req in reqs:
        mux.handle_message({"jsonrpc": "2.0", "id": req["id"], "result": {"i": req["params"]["i"]}})
    results = await asyncio.gather(*tasks)
    assert [r["i"] for r in results] == list(range(n))
    assert mux.pending_count == 0
    assert mux.stats.responses_matched == n


async def test_ids_are_unique_and_never_reused() -> None:
    mux, wire = make()
    for i in range(3):
        t = asyncio.create_task(mux.request("m", timeout=5))
        await wire.wait_for(i + 1)
        rid = wire.requests()[-1]["id"]
        mux.handle_message({"jsonrpc": "2.0", "id": rid, "result": {}})
        await t
    ids = [r["id"] for r in wire.requests()]
    assert len(set(ids)) == len(ids) == 3


async def test_error_response_raises_upstream_rpc_error() -> None:
    mux, wire = make()
    t = asyncio.create_task(mux.request("m", timeout=5))
    await wire.wait_for(1)
    rid = wire.requests()[0]["id"]
    mux.handle_message(
        {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "bad", "data": [1]}}
    )
    with pytest.raises(UpstreamRpcError) as info:
        await t
    assert (info.value.code, info.value.message, info.value.data) == (-32602, "bad", [1])


async def test_malformed_error_object_does_not_hang() -> None:
    mux, wire = make()
    t = asyncio.create_task(mux.request("m", timeout=5))
    await wire.wait_for(1)
    mux.handle_message({"jsonrpc": "2.0", "id": wire.requests()[0]["id"], "error": "boom"})
    with pytest.raises(UpstreamRpcError):
        await t


async def test_duplicate_unknown_and_wrong_type_ids_are_dropped() -> None:
    mux, wire = make()
    t = asyncio.create_task(mux.request("m", timeout=5))
    await wire.wait_for(1)
    rid = wire.requests()[0]["id"]
    mux.handle_message({"jsonrpc": "2.0", "id": 424242, "result": "unknown"})
    mux.handle_message({"jsonrpc": "2.0", "id": str(rid), "result": "string id"})
    mux.handle_message({"jsonrpc": "2.0", "id": True, "result": "bool id"})
    assert not t.done()
    mux.handle_message({"jsonrpc": "2.0", "id": rid, "result": "right"})
    mux.handle_message({"jsonrpc": "2.0", "id": rid, "result": "duplicate"})
    assert await t == "right"
    assert mux.stats.responses_unmatched == 4


async def test_timeout_cleans_up_and_sends_cancel() -> None:
    mux, wire = make()
    with pytest.raises(UpstreamTimeoutError):
        await mux.request("slow", timeout=0.05)
    assert mux.pending_count == 0
    await asyncio.sleep(0)  # let the background cancel notice go out
    rid = wire.requests()[0]["id"]
    cancels = [m for m in wire.sent if m.get("method") == "notifications/cancelled"]
    assert cancels == [
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": rid, "reason": "timeout"},
        }
    ]
    # A late response must be dropped, not delivered anywhere.
    mux.handle_message({"jsonrpc": "2.0", "id": rid, "result": "late"})
    assert mux.stats.responses_unmatched == 1


async def test_initialize_is_never_cancelled() -> None:
    mux, wire = make()
    with pytest.raises(UpstreamTimeoutError):
        await mux.request("initialize", {}, timeout=0.01)
    await asyncio.sleep(0)
    assert not [m for m in wire.sent if m.get("method") == "notifications/cancelled"]


async def test_caller_cancellation_cleans_up_and_notifies() -> None:
    mux, wire = make()
    t = asyncio.create_task(mux.request("slow", timeout=5))
    await wire.wait_for(1)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await asyncio.sleep(0)
    assert mux.pending_count == 0
    assert any(m.get("method") == "notifications/cancelled" for m in wire.sent)


async def test_close_fails_all_pending_and_rejects_new() -> None:
    mux, wire = make()
    tasks = [asyncio.create_task(mux.request("m", timeout=5)) for _ in range(5)]
    await wire.wait_for(5)
    mux.close(UpstreamClosedError("gone"))
    for t in tasks:
        with pytest.raises(UpstreamClosedError):
            await t
    with pytest.raises(UpstreamClosedError):
        await mux.request("m", timeout=1)
    # No cancel notices are sent to a closed connection.
    await asyncio.sleep(0)
    assert not [m for m in wire.sent if m.get("method") == "notifications/cancelled"]


async def test_max_pending_is_enforced() -> None:
    mux, wire = make(max_pending=2)
    a = asyncio.create_task(mux.request("m", timeout=5))
    b = asyncio.create_task(mux.request("m", timeout=5))
    await wire.wait_for(2)
    with pytest.raises(OverloadedError):
        await mux.request("m", timeout=5)
    for req in wire.requests():
        mux.handle_message({"jsonrpc": "2.0", "id": req["id"], "result": 1})
    await asyncio.gather(a, b)
    assert mux.stats.rejected_overload == 1


async def test_notifications_are_dispatched_not_treated_as_responses() -> None:
    seen: list[tuple[str, Any]] = []
    mux, wire = make(on_notification=lambda m, p: seen.append((m, p)))
    t = asyncio.create_task(mux.request("m", timeout=5))
    await wire.wait_for(1)
    mux.handle_message({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
    assert not t.done()
    mux.handle_message({"jsonrpc": "2.0", "id": wire.requests()[0]["id"], "result": "ok"})
    assert await t == "ok"
    assert seen == [("notifications/progress", {"progress": 1})]


async def test_upstream_requests_get_method_not_found_by_default() -> None:
    mux, wire = make()
    mux.handle_message({"jsonrpc": "2.0", "id": "s1", "method": "sampling/createMessage", "params": {}})
    for _ in range(5):
        await asyncio.sleep(0)
    assert wire.sent == [
        {
            "jsonrpc": "2.0",
            "id": "s1",
            "error": {"code": -32601, "message": "method not supported by gateway: sampling/createMessage"},
        }
    ]


@pytest.mark.parametrize(
    "message",
    [
        "string",
        42,
        None,
        {"id": 1, "result": 1},  # missing jsonrpc
        {"jsonrpc": "1.0", "id": 1, "result": 1},
        {"jsonrpc": "2.0"},
        {"jsonrpc": "2.0", "id": 1, "result": 1, "error": {"code": 1, "message": "x"}},
        {"jsonrpc": "2.0", "method": 5},
        [],
    ],
)
async def test_malformed_messages_are_counted_and_ignored(message: Any) -> None:
    mux, _ = make()
    mux.handle_message(message)
    assert mux.stats.malformed_messages == 1


async def test_batch_of_responses_is_handled() -> None:
    mux, wire = make()
    a = asyncio.create_task(mux.request("m", timeout=5))
    b = asyncio.create_task(mux.request("m", timeout=5))
    await wire.wait_for(2)
    ids = [r["id"] for r in wire.requests()]
    mux.handle_message([{"jsonrpc": "2.0", "id": i, "result": i} for i in reversed(ids)])
    assert await a == ids[0]
    assert await b == ids[1]


async def test_send_failure_cleans_up_pending() -> None:
    async def broken(_: dict[str, Any]) -> None:
        raise UpstreamClosedError("pipe closed")

    mux = JsonRpcMultiplexer(send=broken)
    with pytest.raises(UpstreamClosedError):
        await mux.request("m", timeout=1)
    assert mux.pending_count == 0
