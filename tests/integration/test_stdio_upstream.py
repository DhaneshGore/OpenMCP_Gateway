"""Real-subprocess tests of the stdio transport against the scripted fake server."""

from __future__ import annotations

import asyncio
import json
import os
import random
from collections.abc import AsyncIterator
from typing import Any

import pytest

from openmcp_gateway.errors import (
    UpstreamClosedError,
    UpstreamRpcError,
    UpstreamStartError,
    UpstreamTimeoutError,
)
from openmcp_gateway.upstream.stdio import StdioUpstream
from tests.conftest import fake_server_config

pytestmark = pytest.mark.e2e


def make_upstream(mode: str = "normal", **kw: Any) -> StdioUpstream:
    opts: dict[str, Any] = {"max_message_bytes": 64 * 1024, "max_pending": 256}
    opts.update(kw.pop("opts", {}))
    return StdioUpstream(fake_server_config(mode=mode, **kw), **opts)


@pytest.fixture
async def upstream() -> AsyncIterator[StdioUpstream]:
    up = make_upstream()
    await up.start()
    yield up
    await up.stop()


async def call(up: StdioUpstream, name: str, timeout: float = 5, **args: Any) -> Any:
    return await up.request("tools/call", {"name": name, "arguments": args}, timeout=timeout)


def text(result: dict[str, Any]) -> str:
    return result["content"][0]["text"]


async def test_initialize_records_server_info(upstream: StdioUpstream) -> None:
    assert upstream.protocol_version == "2025-11-25"
    assert upstream.server_info is not None and upstream.server_info.name == "fake"
    assert upstream.alive


async def test_many_concurrent_out_of_order_calls(upstream: StdioUpstream) -> None:
    rng = random.Random(7)
    n = 100
    delays = [rng.uniform(0, 0.3) for _ in range(n)]
    results = await asyncio.gather(
        *(call(upstream, "sleep", seconds=d, text=f"r{i}") for i, d in enumerate(delays))
    )
    assert [text(r) for r in results] == [f"r{i}" for i in range(n)]
    assert upstream.mux.pending_count == 0


async def test_wrong_and_duplicate_ids_do_not_cross_wires(upstream: StdioUpstream) -> None:
    assert text(await call(upstream, "wrong_id")) == "right"
    assert text(await call(upstream, "dup")) == "first"
    await asyncio.sleep(0.1)
    assert upstream.mux.stats.responses_unmatched >= 4


async def test_malformed_output_is_skipped(upstream: StdioUpstream) -> None:
    assert text(await call(upstream, "garbage")) == "after garbage"
    assert upstream.mux.stats.malformed_messages >= 4
    assert upstream.alive


async def test_error_response_is_forwarded(upstream: StdioUpstream) -> None:
    with pytest.raises(UpstreamRpcError) as info:
        await call(upstream, "error")
    assert info.value.code == -32602 and info.value.data == {"x": 1}


async def test_timeout_sends_cancel_and_late_response_is_dropped(upstream: StdioUpstream) -> None:
    with pytest.raises(UpstreamTimeoutError):
        await call(upstream, "sleep", timeout=0.2, seconds=0.5, text="late")
    await asyncio.sleep(0.5)
    cancelled = json.loads(text(await call(upstream, "cancelled")))
    assert len(cancelled) == 1
    assert upstream.mux.pending_count == 0


async def test_crash_fails_all_pending_requests() -> None:
    exits: list[StdioUpstream] = []
    up = make_upstream(opts={"on_exit": exits.append})
    await up.start()
    try:
        slow = [asyncio.create_task(call(up, "sleep", seconds=3, text="x")) for _ in range(5)]
        await asyncio.sleep(0.2)
        with pytest.raises(UpstreamClosedError):
            await call(up, "crash")
        for t in slow:
            with pytest.raises(UpstreamClosedError):
                await t
        assert exits == [up]
        assert up.returncode == 3
        with pytest.raises(UpstreamClosedError):
            await call(up, "echo", text="after crash")
    finally:
        await up.stop()


async def test_oversize_message_is_discarded_and_connection_survives() -> None:
    up = make_upstream(opts={"max_message_bytes": 4096})
    await up.start()
    try:
        with pytest.raises(UpstreamTimeoutError):
            await call(up, "big", timeout=1, bytes=100_000)
        assert up.oversize_messages == 1
        assert text(await call(up, "echo", text="still fine")) == "still fine"
    finally:
        await up.stop()


async def test_stderr_flood_is_bounded_and_does_not_block() -> None:
    up = make_upstream(opts={"stderr_lines": 50, "stderr_line_bytes": 64})
    await up.start()
    try:
        assert text(await call(up, "stderr_flood", lines=20_000)) == "flooded 20000"
        await asyncio.sleep(0.2)
        assert len(up.stderr) == 50
        assert all(len(line.text) <= 64 for line in up.stderr)
        assert all(line.truncated for line in up.stderr)
    finally:
        await up.stop()


async def test_environment_is_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPER_SECRET_GATEWAY_VAR", "leak-me-if-you-can")
    monkeypatch.setenv("FOR_CHILD", "child-secret-value")
    up = make_upstream(env_from_host={"CHILD_TOKEN": "FOR_CHILD"}, env={"PLAIN": "1"})
    await up.start()
    try:
        env = json.loads(text(await call(up, "env")))
        assert "SUPER_SECRET_GATEWAY_VAR" not in env
        assert env["CHILD_TOKEN"] == "child-secret-value"
        assert env["PLAIN"] == "1"
    finally:
        await up.stop()


async def test_shell_metacharacters_are_passed_literally(tmp_path: Any) -> None:
    marker = tmp_path / "pwned"
    evil = [f"; touch {marker}", f"$(touch {marker})", f"`touch {marker}`", "| cat /etc/passwd", "&& id"]
    cfg_args = ["normal", *evil]
    up = make_upstream()
    up.config = up.config.model_copy(update={"args": [up.config.args[0], *cfg_args]})
    await up.start()
    try:
        argv = json.loads(text(await call(up, "argv")))
        assert argv == cfg_args
        assert not marker.exists()
    finally:
        await up.stop()


async def test_upstream_requests_are_refused(upstream: StdioUpstream) -> None:
    reply = json.loads(text(await call(upstream, "ask_client")))
    assert reply["error"]["code"] == -32601


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("crash-on-start", "exited with code 7"),
        ("bad-version", "unsupported protocol version"),
        ("init-error", "initialize rejected"),
    ],
)
async def test_startup_failures_are_reported(mode: str, message: str) -> None:
    up = make_upstream(mode=mode)
    with pytest.raises(UpstreamStartError, match=message):
        await up.start()
    assert not up.alive


async def test_garbage_before_initialize_is_tolerated() -> None:
    up = make_upstream(mode="garbage-init")
    await up.start()
    try:
        assert up.alive
    finally:
        await up.stop()


async def test_startup_timeout() -> None:
    up = make_upstream(mode="hang-init", timeouts={"startup": 0.5, "request": 1, "shutdown": 1})
    with pytest.raises(UpstreamStartError, match="did not answer initialize"):
        await up.start()
    assert up.returncode is not None


async def test_command_not_found() -> None:
    up = make_upstream()
    up.config = up.config.model_copy(update={"command": "definitely-not-a-real-binary-xyz"})
    with pytest.raises(UpstreamStartError, match="command not found"):
        await up.start()


async def test_stop_terminates_process_group(upstream: StdioUpstream) -> None:
    pid = upstream.pid
    assert pid is not None
    await upstream.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
