"""Lifecycle management for upstream servers.

Responsibilities: single-flight starts (concurrent callers share one start
attempt), restart policy with exponential backoff and a hard cap, per-server
concurrency limits with a bounded wait queue, capability/tool caches, and
orderly shutdown. All state here is process-local.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import mcp_types as types
from opentelemetry import trace
from pydantic import ValidationError

from openmcp_gateway.config import AnyServerConfig, GatewayConfig, HttpServerConfig, PackageServerConfig
from openmcp_gateway.errors import (
    GatewayError,
    OverloadedError,
    UpstreamProtocolError,
    UpstreamStartError,
    UpstreamUnavailableError,
)
from openmcp_gateway.packages.runtime import PackageRuntime, PackageUpstream
from openmcp_gateway.security.ssrf import EgressPolicy
from openmcp_gateway.upstream.base import UpstreamConnection
from openmcp_gateway.upstream.http import HttpUpstream
from openmcp_gateway.upstream.stdio import StdioUpstream

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("openmcp_gateway")

# Requests the gateway itself issues (tools/list, ping) get this many pending
# slots on top of the per-server concurrency limit.
_INTERNAL_PENDING_HEADROOM = 8
_MAX_LIST_PAGES = 100


class ServerState(enum.StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    FAILED = "failed"
    DISABLED = "disabled"


UpstreamFactory = Callable[["ManagedServer"], UpstreamConnection]
NotificationListener = Callable[[str, str, Any], None]  # (server_id, method, params)
# (server_id, method, exception or None, seconds)
RequestObserver = Callable[[str, str, BaseException | None, float], None]


@dataclass
class ManagedServer:
    config: AnyServerConfig
    source: str = "config"  # "config" (read-only) or "registry" (admin API)
    state: ServerState = ServerState.STOPPED
    upstream: UpstreamConnection | None = None
    last_error: str | None = None
    failures: deque[float] = field(default_factory=deque)
    next_start_allowed_at: float = 0.0
    restarts_exhausted: bool = False
    held: bool = False  # stopped by an operator; only an explicit start clears it
    tools: list[types.Tool] | None = None
    prompts: list[types.Prompt] | None = None
    resources: list[types.Resource] | None = None
    _start_task: asyncio.Task[None] | None = None
    _restart_handle: asyncio.TimerHandle | None = None
    _semaphore: asyncio.Semaphore = field(init=False)
    waiting: int = 0
    in_flight: int = 0

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)
        if not self.config.enabled:
            self.state = ServerState.DISABLED

    @property
    def id(self) -> str:
        return self.config.id

    def invalidate_caches(self) -> None:
        self.tools = None
        self.prompts = None
        self.resources = None


class ServerManager:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        upstream_factory: UpstreamFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
        package_runtime: PackageRuntime | None = None,
    ) -> None:
        self._config = config
        self.package_runtime = package_runtime
        self._clock = clock
        self._factory = upstream_factory or self._default_factory
        self._servers: dict[str, ManagedServer] = {s.id: ManagedServer(s) for s in config.servers}
        self._listeners: list[NotificationListener] = []
        self._observers: list[RequestObserver] = []
        self._closing = False

    # ------------------------------------------------------------ queries

    def get(self, server_id: str) -> ManagedServer | None:
        return self._servers.get(server_id)

    def all(self) -> list[ManagedServer]:
        return list(self._servers.values())

    def add_listener(self, listener: NotificationListener) -> None:
        self._listeners.append(listener)

    def package_users(self, name: str, version: str) -> list[str]:
        """Ids of servers (any source) that run package `name` at `version`."""
        return [
            s.id
            for s in self._servers.values()
            if isinstance(s.config, PackageServerConfig)
            and s.config.package == name
            and s.config.version == version
        ]

    def add_observer(self, observer: RequestObserver) -> None:
        self._observers.append(observer)

    # ------------------------------------------------------------ lifecycle

    async def start_autostart(self) -> None:
        """Start every enabled autostart server concurrently; failures are logged, not raised."""
        targets = [s for s in self._servers.values() if s.config.enabled and s.config.autostart]
        results = await asyncio.gather(*(self.ensure_ready(s.id) for s in targets), return_exceptions=True)
        for server, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "server failed to start",
                    extra={"server": server.id, "error": _describe(result)},
                )

    async def ensure_ready(self, server_id: str) -> ManagedServer:
        """Return a READY server, starting it if needed (single-flight)."""
        server = self._servers.get(server_id)
        if server is None:
            raise UpstreamUnavailableError(f"unknown server {server_id!r}")
        if self._closing:
            raise UpstreamUnavailableError("gateway is shutting down")
        if server.state is ServerState.DISABLED:
            raise UpstreamUnavailableError(f"server {server_id!r} is disabled")
        if server.held:
            raise UpstreamUnavailableError(f"server {server_id!r} was stopped by an operator")
        if server.state is ServerState.READY and server.upstream is not None and server.upstream.alive:
            return server

        if server._start_task is None or server._start_task.done():
            if server.state is ServerState.FAILED:
                self._check_restart_allowed(server)
            server._start_task = asyncio.create_task(self._start(server), name=f"start-{server_id}")
        # Shield: one caller giving up must not abort a start others are awaiting.
        await asyncio.shield(server._start_task)
        return server

    async def _start(self, server: ManagedServer) -> None:
        if server._restart_handle is not None:
            server._restart_handle.cancel()
            server._restart_handle = None
        server.state = ServerState.STARTING
        server.invalidate_caches()
        upstream = self._factory(server)
        try:
            await upstream.start()
        except BaseException as exc:
            message = exc.public_message if isinstance(exc, GatewayError) else "unexpected start failure"
            if not isinstance(exc, GatewayError | asyncio.CancelledError):
                logger.exception("unexpected error starting server", extra={"server": server.id})
            self._record_failure(server, message)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise UpstreamStartError(message) from None
        server.upstream = upstream
        server.state = ServerState.READY
        server.last_error = None
        logger.info(
            "server ready",
            extra={
                "server": server.id,
                "protocol_version": upstream.protocol_version,
                "upstream_name": upstream.server_info.name if upstream.server_info else None,
            },
        )

    async def stop(self, server_id: str) -> None:
        """Operator-initiated stop: the server stays stopped until started explicitly."""
        server = self._servers.get(server_id)
        if server is None:
            raise UpstreamUnavailableError(f"unknown server {server_id!r}")
        server.held = True
        await self._stop(server)

    async def _stop(self, server: ManagedServer) -> None:
        if server._restart_handle is not None:
            server._restart_handle.cancel()
            server._restart_handle = None
        if server._start_task is not None and not server._start_task.done():
            with contextlib.suppress(BaseException):
                await asyncio.shield(server._start_task)
        upstream = server.upstream
        if upstream is None:
            if server.state is not ServerState.DISABLED:
                server.state = ServerState.STOPPED
            return
        server.state = ServerState.STOPPING
        try:
            await upstream.stop()
        finally:
            server.upstream = None
            server.invalidate_caches()
            server.state = ServerState.DISABLED if not server.config.enabled else ServerState.STOPPED

    async def start(self, server_id: str) -> ManagedServer:
        """Operator-initiated start: clears restart history and backoff."""
        server = self._servers.get(server_id)
        if server is None:
            raise UpstreamUnavailableError(f"unknown server {server_id!r}")
        server.held = False
        server.failures.clear()
        server.restarts_exhausted = False
        server.next_start_allowed_at = 0.0
        if server.state is ServerState.FAILED:
            server.state = ServerState.STOPPED
        return await self.ensure_ready(server_id)

    # ------------------------------------------------------------ registry

    def add_server(self, config: AnyServerConfig, *, source: str) -> ManagedServer:
        if config.id in self._servers:
            raise ValueError(f"server {config.id!r} already exists")
        server = ManagedServer(config, source=source)
        self._servers[config.id] = server
        return server

    async def remove_server(self, server_id: str) -> None:
        server = self._servers.get(server_id)
        if server is None:
            raise UpstreamUnavailableError(f"unknown server {server_id!r}")
        await self._stop(server)
        self._servers.pop(server_id, None)

    async def replace_server(self, config: AnyServerConfig) -> ManagedServer:
        old = self._servers.get(config.id)
        source = old.source if old else "registry"
        if old is not None:
            await self._stop(old)
            self._servers.pop(config.id, None)
        return self.add_server(config, source=source)

    async def shutdown(self) -> None:
        self._closing = True
        await asyncio.gather(*(self._stop(s) for s in self._servers.values()), return_exceptions=True)

    # ------------------------------------------------------------ failures

    def _record_failure(self, server: ManagedServer, message: str) -> None:
        now = self._clock()
        policy = server.config.restart
        server.failures.append(now)
        while server.failures and now - server.failures[0] > policy.window:
            server.failures.popleft()
        server.state = ServerState.FAILED
        server.last_error = message
        server.upstream = None
        server.invalidate_caches()
        if policy.policy == "never" or len(server.failures) > policy.max_restarts:
            server.restarts_exhausted = True
            logger.error(
                "server failed; not restarting automatically",
                extra={"server": server.id, "failures_in_window": len(server.failures)},
            )
            return
        delay = min(policy.backoff_max, policy.backoff_initial * 2 ** (len(server.failures) - 1))
        server.next_start_allowed_at = now + delay
        if server.config.autostart and not self._closing:
            loop = asyncio.get_running_loop()
            server._restart_handle = loop.call_later(delay, self._background_restart, server)

    def _check_restart_allowed(self, server: ManagedServer) -> None:
        if server.restarts_exhausted:
            raise UpstreamUnavailableError(
                f"server {server.id!r} failed repeatedly and will not be restarted automatically"
            )
        wait = server.next_start_allowed_at - self._clock()
        if wait > 0:
            raise UpstreamUnavailableError(f"server {server.id!r} is unavailable; retry in {wait:.0f}s")

    def _background_restart(self, server: ManagedServer) -> None:
        server._restart_handle = None
        if self._closing or server.held or server.state is not ServerState.FAILED:
            return
        task = asyncio.ensure_future(self.ensure_ready(server.id))
        task.add_done_callback(_swallow)

    def _on_upstream_exit(self, server: ManagedServer, upstream: UpstreamConnection) -> None:
        if server.upstream is not upstream:
            return  # stale callback from a replaced process
        code = upstream.returncode
        self._record_failure(server, "process exited" + ("" if code is None else f" with code {code}"))

    # ------------------------------------------------------------ requests

    @contextlib.asynccontextmanager
    async def slot(self, server: ManagedServer) -> AsyncIterator[None]:
        """Per-server concurrency slot with a bounded, time-limited wait queue."""
        sem = server._semaphore
        if sem.locked():
            if server.waiting >= server.config.max_queue:
                raise OverloadedError(f"server {server.id!r} is at capacity")
            server.waiting += 1
            try:
                async with asyncio.timeout(server.config.queue_timeout):
                    await sem.acquire()
            except TimeoutError:
                raise OverloadedError(f"server {server.id!r} is at capacity") from None
            finally:
                server.waiting -= 1
        else:
            await sem.acquire()
        server.in_flight += 1
        try:
            yield
        finally:
            server.in_flight -= 1
            sem.release()

    async def request(
        self, server_id: str, method: str, params: Any = None, *, timeout: float | None = None
    ) -> Any:
        started = time.perf_counter()
        error: BaseException | None = None
        with tracer.start_as_current_span(
            f"upstream {method}", attributes={"mcp.server.id": server_id, "rpc.method": method}
        ):
            try:
                server = await self.ensure_ready(server_id)
                async with self.slot(server):
                    upstream = server.upstream
                    if upstream is None:
                        raise UpstreamUnavailableError(f"server {server_id!r} is not running")
                    return await upstream.request(method, params, timeout=timeout)
            except BaseException as exc:
                error = exc
                raise
            finally:
                elapsed = time.perf_counter() - started
                for observer in self._observers:
                    observer(server_id, method, error, elapsed)

    async def list_tools(self, server_id: str) -> list[types.Tool]:
        server = await self.ensure_ready(server_id)
        if server.tools is not None:
            return server.tools
        caps = server.upstream.capabilities if server.upstream else None
        if caps is None or caps.tools is None:
            server.tools = []
            return []
        raw = await self._list_all(server, "tools/list", "tools")
        tools: list[types.Tool] = []
        for item in raw:
            try:
                tools.append(types.Tool.model_validate(item))
            except ValidationError:
                logger.warning("skipping invalid tool definition", extra={"server": server.id})
        server.tools = tools
        return tools

    async def list_prompts(self, server_id: str) -> list[types.Prompt]:
        server = await self.ensure_ready(server_id)
        if server.prompts is not None:
            return server.prompts
        caps = server.upstream.capabilities if server.upstream else None
        if caps is None or caps.prompts is None:
            server.prompts = []
            return []
        raw = await self._list_all(server, "prompts/list", "prompts")
        prompts: list[types.Prompt] = []
        for item in raw:
            try:
                prompts.append(types.Prompt.model_validate(item))
            except ValidationError:
                logger.warning("skipping invalid prompt definition", extra={"server": server.id})
        server.prompts = prompts
        return prompts

    async def list_resources(self, server_id: str) -> list[types.Resource]:
        server = await self.ensure_ready(server_id)
        if server.resources is not None:
            return server.resources
        caps = server.upstream.capabilities if server.upstream else None
        if caps is None or caps.resources is None:
            server.resources = []
            return []
        raw = await self._list_all(server, "resources/list", "resources")
        resources: list[types.Resource] = []
        for item in raw:
            try:
                resources.append(types.Resource.model_validate(item))
            except ValidationError:
                logger.warning("skipping invalid resource definition", extra={"server": server.id})
        server.resources = resources
        return resources

    async def _list_all(self, server: ManagedServer, method: str, key: str) -> list[Any]:
        """Follow pagination cursors, bounded in pages and items."""
        limit = self._config.limits.max_tools_per_server
        items: list[Any] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(_MAX_LIST_PAGES):
            params = {"cursor": cursor} if cursor else None
            result = await self.request(server.id, method, params)
            if not isinstance(result, dict) or not isinstance(result.get(key), list):
                raise UpstreamProtocolError(f"server {server.id!r} returned an invalid {method} result")
            items.extend(result[key])
            if len(items) > limit:
                logger.warning(
                    "upstream list truncated", extra={"server": server.id, "method": method, "limit": limit}
                )
                return items[:limit]
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str) or cursor in seen:
                break
            seen.add(cursor)
        return items

    # ------------------------------------------------------------ wiring

    def _default_factory(self, server: ManagedServer) -> UpstreamConnection:
        cfg = self._config

        def on_notification(method: str, params: Any) -> None:
            if method == "notifications/tools/list_changed":
                server.tools = None
            elif method == "notifications/prompts/list_changed":
                server.prompts = None
            elif method == "notifications/resources/list_changed":
                server.resources = None
            for listener in self._listeners:
                try:
                    listener(server.id, method, params)
                except Exception:
                    logger.exception("notification listener failed")

        def on_exit(upstream: UpstreamConnection) -> None:
            self._on_upstream_exit(server, upstream)

        if isinstance(server.config, PackageServerConfig):
            if self.package_runtime is None:
                raise UpstreamStartError(f"server {server.id!r}: packages are not enabled on this gateway")
            return PackageUpstream(
                server.config,
                self.package_runtime,
                max_message_bytes=cfg.limits.max_upstream_message_bytes,
                max_pending=server.config.max_concurrency + _INTERNAL_PENDING_HEADROOM,
                stderr_lines=cfg.logging.stderr_buffer_lines,
                stderr_line_bytes=cfg.logging.stderr_line_bytes,
                on_notification=on_notification,
                on_exit=on_exit,
            )
        if isinstance(server.config, HttpServerConfig):
            return HttpUpstream(
                server.config,
                policy=EgressPolicy(allowed_networks=tuple(cfg.security.allowed_egress_networks)),
                max_message_bytes=cfg.limits.max_upstream_message_bytes,
                on_notification=on_notification,
                on_exit=on_exit,
            )
        return StdioUpstream(
            server.config,
            max_message_bytes=cfg.limits.max_upstream_message_bytes,
            max_pending=server.config.max_concurrency + _INTERNAL_PENDING_HEADROOM,
            stderr_lines=cfg.logging.stderr_buffer_lines,
            stderr_line_bytes=cfg.logging.stderr_line_bytes,
            on_notification=on_notification,
            on_exit=on_exit,
        )


def _describe(exc: BaseException) -> str:
    return exc.public_message if isinstance(exc, GatewayError) else type(exc).__name__


def _swallow(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()
