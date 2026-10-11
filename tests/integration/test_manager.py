from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from openmcp_gateway.errors import OverloadedError, UpstreamClosedError, UpstreamUnavailableError
from openmcp_gateway.runtime.manager import ManagedServer, ServerManager, ServerState
from openmcp_gateway.upstream.stdio import StdioUpstream
from tests.conftest import fake_server_config, gateway_config

pytestmark = pytest.mark.e2e


class CountingFactory:
    """Wraps the default factory to count process launches."""

    def __init__(self) -> None:
        self.launches = 0
        self.manager: ServerManager | None = None

    def __call__(self, server: ManagedServer) -> StdioUpstream:
        self.launches += 1
        assert self.manager is not None
        return self.manager._default_factory(server)


async def build(*servers: Any) -> tuple[ServerManager, CountingFactory]:
    factory = CountingFactory()
    manager = ServerManager(gateway_config(list(servers)), upstream_factory=factory)
    factory.manager = manager
    return manager, factory


@pytest.fixture
async def cleanup() -> AsyncIterator[list[ServerManager]]:
    managers: list[ServerManager] = []
    yield managers
    for m in managers:
        await m.shutdown()


async def test_concurrent_callers_share_one_start(cleanup: list[ServerManager]) -> None:
    manager, factory = await build(fake_server_config("a"))
    cleanup.append(manager)
    servers = await asyncio.gather(*(manager.ensure_ready("a") for _ in range(25)))
    assert factory.launches == 1
    assert all(s.state is ServerState.READY for s in servers)


async def test_cancelled_caller_does_not_abort_shared_start(cleanup: list[ServerManager]) -> None:
    manager, factory = await build(fake_server_config("a"))
    cleanup.append(manager)
    first = asyncio.create_task(manager.ensure_ready("a"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(manager.ensure_ready("a"))
    first.cancel()
    server = await second
    assert server.state is ServerState.READY
    assert factory.launches == 1


async def test_crash_triggers_backoff_then_background_restart(cleanup: list[ServerManager]) -> None:
    cfg = fake_server_config("a", restart={"backoff_initial": 0.3, "backoff_max": 1, "max_restarts": 3})
    manager, factory = await build(cfg)
    cleanup.append(manager)
    await manager.ensure_ready("a")
    with pytest.raises(UpstreamClosedError):
        await manager.request("a", "tools/call", {"name": "crash"})
    await asyncio.sleep(0.05)
    server = manager.get("a")
    assert server is not None and server.state is ServerState.FAILED
    with pytest.raises(UpstreamUnavailableError, match="retry in"):
        await manager.ensure_ready("a")
    await asyncio.sleep(1.0)
    assert server.state is ServerState.READY
    assert factory.launches == 2


async def test_restart_limit_is_enforced_and_operator_start_resets(cleanup: list[ServerManager]) -> None:
    cfg = fake_server_config(
        "a", mode="crash-on-start", autostart=False, restart={"max_restarts": 1, "backoff_initial": 0.05}
    )
    manager, factory = await build(cfg)
    cleanup.append(manager)
    with pytest.raises(UpstreamUnavailableError):
        await manager.ensure_ready("a")
    await asyncio.sleep(0.1)
    with pytest.raises(UpstreamUnavailableError):
        await manager.ensure_ready("a")
    server = manager.get("a")
    assert server is not None and server.restarts_exhausted
    with pytest.raises(UpstreamUnavailableError, match="will not be restarted"):
        await manager.ensure_ready("a")
    assert factory.launches == 2
    # Operator start clears history and tries again.
    with pytest.raises(UpstreamUnavailableError):
        await manager.start("a")
    assert factory.launches == 3


async def test_never_policy_does_not_restart(cleanup: list[ServerManager]) -> None:
    cfg = fake_server_config("a", restart={"policy": "never"})
    manager, factory = await build(cfg)
    cleanup.append(manager)
    await manager.ensure_ready("a")
    with pytest.raises(UpstreamClosedError):
        await manager.request("a", "tools/call", {"name": "crash"})
    await asyncio.sleep(0.3)
    with pytest.raises(UpstreamUnavailableError):
        await manager.ensure_ready("a")
    assert factory.launches == 1


async def test_concurrency_limit_and_bounded_queue(cleanup: list[ServerManager]) -> None:
    cfg = fake_server_config("a", max_concurrency=2, max_queue=1, queue_timeout=5)
    manager, _ = await build(cfg)
    cleanup.append(manager)
    await manager.ensure_ready("a")
    slow = {"name": "sleep", "arguments": {"seconds": 0.5, "text": "s"}}
    running = [asyncio.create_task(manager.request("a", "tools/call", slow)) for _ in range(2)]
    await asyncio.sleep(0.1)
    queued = asyncio.create_task(manager.request("a", "tools/call", slow))
    await asyncio.sleep(0.05)
    with pytest.raises(OverloadedError):
        await manager.request("a", "tools/call", slow)
    await asyncio.gather(*running, queued)
    server = manager.get("a")
    assert server is not None and server.in_flight == 0


async def test_queue_timeout(cleanup: list[ServerManager]) -> None:
    cfg = fake_server_config("a", max_concurrency=1, max_queue=5, queue_timeout=0.2)
    manager, _ = await build(cfg)
    cleanup.append(manager)
    await manager.ensure_ready("a")
    blocker = asyncio.create_task(
        manager.request("a", "tools/call", {"name": "sleep", "arguments": {"seconds": 1, "text": "x"}})
    )
    await asyncio.sleep(0.1)
    with pytest.raises(OverloadedError):
        await manager.request("a", "tools/call", {"name": "echo", "arguments": {"text": "y"}})
    await blocker


async def test_disabled_server_is_unavailable(cleanup: list[ServerManager]) -> None:
    manager, factory = await build(fake_server_config("a", enabled=False))
    cleanup.append(manager)
    await manager.start_autostart()
    with pytest.raises(UpstreamUnavailableError, match="disabled"):
        await manager.ensure_ready("a")
    assert factory.launches == 0


async def test_tools_cache_invalidated_by_list_changed(cleanup: list[ServerManager]) -> None:
    manager, _ = await build(fake_server_config("a"))
    cleanup.append(manager)
    tools = await manager.list_tools("a")
    assert "echo" in {t.name for t in tools}
    server = manager.get("a")
    assert server is not None and server.tools is not None
    await manager.request("a", "tools/call", {"name": "list_changed"})
    await asyncio.sleep(0.05)
    assert server.tools is None


async def test_shutdown_stops_everything() -> None:
    manager, _ = await build(fake_server_config("a"), fake_server_config("b"))
    await manager.start_autostart()
    pids = [s.upstream.pid for s in manager.all() if s.upstream]
    assert len(pids) == 2
    await manager.shutdown()
    assert all(s.state is ServerState.STOPPED for s in manager.all())
    with pytest.raises(UpstreamUnavailableError, match="shutting down"):
        await manager.ensure_ready("a")


async def test_operator_stop_is_not_undone_by_lazy_start(cleanup: list[ServerManager]) -> None:
    manager, factory = await build(fake_server_config("a"))
    cleanup.append(manager)
    await manager.ensure_ready("a")
    await manager.stop("a")
    with pytest.raises(UpstreamUnavailableError, match="stopped by an operator"):
        await manager.request("a", "tools/call", {"name": "echo", "arguments": {"text": "x"}})
    assert factory.launches == 1
    await manager.start("a")
    assert factory.launches == 2


async def test_lazy_start_for_non_autostart_servers(cleanup: list[ServerManager]) -> None:
    manager, factory = await build(fake_server_config("a", autostart=False))
    cleanup.append(manager)
    await manager.start_autostart()
    assert factory.launches == 0
    result = await manager.request("a", "tools/call", {"name": "echo", "arguments": {"text": "lazy"}})
    assert result["content"][0]["text"] == "lazy"
    assert factory.launches == 1
