"""Stdio transport: an MCP server running as a local subprocess.

Framing per the MCP stdio transport: UTF-8 JSON-RPC messages delimited by
newlines; the server may write anything to stderr. This module owns the
process, a stdout reader that feeds the multiplexer, a bounded stderr capture,
and graceful termination of the whole process group.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp_types as types
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_HANDSHAKE_VERSION
from pydantic import ValidationError

from openmcp_gateway import PROJECT_SLUG, __version__
from openmcp_gateway.config import PackageServerConfig, StdioServerConfig
from openmcp_gateway.errors import (
    GatewayError,
    UpstreamClosedError,
    UpstreamProtocolError,
    UpstreamRpcError,
    UpstreamStartError,
)
from openmcp_gateway.redaction import redactor
from openmcp_gateway.security.env import build_child_env
from openmcp_gateway.upstream.base import StderrLine
from openmcp_gateway.upstream.jsonrpc import JsonObject, JsonRpcMultiplexer, NotificationHandler

logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024


class LineSplitter:
    """Incremental newline splitter with a hard per-line byte limit.

    Lines longer than `max_len` are reported once via `oversize` with their
    first `max_len` bytes; the remainder up to the next newline is discarded.
    Memory use never exceeds `max_len` + one read chunk.
    """

    def __init__(self, max_len: int) -> None:
        self.max_len = max_len
        self._buf = bytearray()
        self._discarding = False

    def feed(self, chunk: bytes) -> list[tuple[bytes, bool]]:
        """Return (line, oversize) pairs completed by this chunk."""
        out: list[tuple[bytes, bool]] = []
        start = 0
        while start <= len(chunk):
            nl = chunk.find(b"\n", start)
            piece = chunk[start:] if nl == -1 else chunk[start:nl]
            if not self._discarding:
                room = self.max_len - len(self._buf)
                if len(piece) > room:
                    self._buf += piece[:room]
                    out.append((bytes(self._buf), True))
                    self._buf.clear()
                    self._discarding = True
                else:
                    self._buf += piece
            if nl == -1:
                break
            if self._discarding:
                self._discarding = False
            else:
                out.append((bytes(self._buf), False))
                self._buf.clear()
            start = nl + 1
        return out

    def flush(self) -> bytes | None:
        """Return a trailing unterminated line at EOF, if any."""
        if self._discarding or not self._buf:
            return None
        data = bytes(self._buf)
        self._buf.clear()
        return data


@dataclass(frozen=True)
class LaunchSpec:
    """A fully resolved process launch (used by package servers)."""

    executable: str
    args: list[str]
    cwd: Path | None
    env: dict[str, str]


class StdioUpstream:
    """One running (or startable) stdio MCP server."""

    def __init__(
        self,
        config: StdioServerConfig | PackageServerConfig,
        *,
        max_message_bytes: int,
        max_pending: int,
        stderr_lines: int = 200,
        stderr_line_bytes: int = 2048,
        on_notification: NotificationHandler | None = None,
        on_exit: Callable[[StdioUpstream], None] | None = None,
        host_env: dict[str, str] | None = None,
        launch: LaunchSpec | None = None,
    ) -> None:
        if launch is None and not isinstance(config, StdioServerConfig):
            raise TypeError("a LaunchSpec is required for non-stdio configurations")
        self.config = config
        self._launch = launch
        self.server_id = config.id
        self._max_message_bytes = max_message_bytes
        self._stderr_line_bytes = stderr_line_bytes
        self._on_notification = on_notification
        self._on_exit = on_exit
        self._host_env = host_env
        self._stderr: deque[StderrLine] = deque(maxlen=max(stderr_lines, 1))
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._write_lock = asyncio.Lock()
        self._stopping = False
        self._exited = asyncio.Event()
        self.mux = JsonRpcMultiplexer(
            send=self._send,
            max_pending=max_pending,
            name=config.id,
            on_notification=self._dispatch_notification,
            on_request=self._answer_upstream_request,
        )
        self.protocol_version: str | None = None
        self.server_info: types.Implementation | None = None
        self.capabilities: types.ServerCapabilities | None = None
        self.instructions: str | None = None
        self.started_at: float | None = None
        self.oversize_messages = 0

    # ------------------------------------------------------------ status

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and not self.mux.closed

    @property
    def stderr(self) -> deque[StderrLine]:
        return self._stderr

    def stats(self) -> dict[str, int]:
        return {**self.mux.stats.as_dict(), "oversize_messages": self.oversize_messages}

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Spawn the process and complete the MCP initialize handshake."""
        cfg = self.config
        launch = self._launch or self._resolve_launch()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                launch.executable,
                *launch.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=launch.env,
                cwd=str(launch.cwd) if launch.cwd else None,
                # Own process group so shutdown can signal the whole tree.
                start_new_session=os.name == "posix",
            )
        except (OSError, ValueError) as exc:
            raise UpstreamStartError(
                f"server {cfg.id!r}: failed to launch {launch.executable!r}: {type(exc).__name__}"
            ) from None

        logger.info("started upstream process", extra={"server": cfg.id, "pid": self._proc.pid})
        self._tasks = [
            asyncio.create_task(self._read_stdout(), name=f"{cfg.id}-stdout"),
            asyncio.create_task(self._read_stderr(), name=f"{cfg.id}-stderr"),
        ]
        try:
            await self._initialize(cfg.timeouts.startup)
        except BaseException as exc:
            await self.stop()
            if isinstance(exc, GatewayError):
                # Messages from this module and the multiplexer already name the server.
                raise UpstreamStartError(exc.public_message) from None
            if isinstance(exc, UpstreamRpcError):
                raise UpstreamStartError(
                    f"server {cfg.id!r}: initialize rejected ({exc.code}: {exc.message})"
                ) from None
            raise
        self.started_at = time.time()

    def _resolve_launch(self) -> LaunchSpec:
        cfg = self.config
        assert isinstance(cfg, StdioServerConfig)
        env, secrets = build_child_env(cfg, self._host_env)
        redactor.register(secrets)
        executable = _resolve_executable(cfg.command, env.get("PATH", ""), cfg.cwd)
        if executable is None:
            raise UpstreamStartError(f"server {cfg.id!r}: command not found: {cfg.command!r}")
        return LaunchSpec(executable=executable, args=list(cfg.args), cwd=cfg.cwd, env=env)

    async def _initialize(self, timeout: float) -> None:
        params = {
            "protocolVersion": LATEST_HANDSHAKE_VERSION,
            # The gateway does not proxy sampling/roots/elicitation upstream yet,
            # so it advertises no client capabilities.
            "capabilities": {},
            "clientInfo": {"name": PROJECT_SLUG, "version": __version__},
        }
        raw = await self.mux.request("initialize", params, timeout=timeout)
        try:
            result = types.InitializeResult.model_validate(raw)
        except ValidationError:
            raise UpstreamProtocolError(f"server {self.server_id!r}: invalid initialize result") from None
        if result.protocol_version not in HANDSHAKE_PROTOCOL_VERSIONS:
            raise UpstreamProtocolError(
                f"server {self.server_id!r}: unsupported protocol version {result.protocol_version!r}"
            )
        self.protocol_version = result.protocol_version
        self.server_info = result.server_info
        self.capabilities = result.capabilities
        self.instructions = result.instructions
        await self.mux.notify("notifications/initialized")

    async def request(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        return await self.mux.request(
            method, params, timeout=timeout if timeout is not None else self.config.timeouts.request
        )

    async def stop(self) -> None:
        """Stop the server: close stdin, then SIGTERM, then SIGKILL the group."""
        self._stopping = True
        self.mux.close(UpstreamClosedError(f"server {self.server_id!r} is stopping"))
        proc = self._proc
        grace = self.config.timeouts.shutdown
        if proc is not None:
            if proc.stdin is not None and not proc.stdin.is_closing():
                with contextlib.suppress(Exception):
                    proc.stdin.close()
            if not await _wait_exit(proc, grace):
                _signal_group(proc, signal.SIGTERM)
                if not await _wait_exit(proc, grace):
                    _signal_group(proc, signal.SIGKILL)
                    await _wait_exit(proc, grace)
            # Reap any children left in the group even after a clean exit.
            _signal_group(proc, signal.SIGTERM)
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.mux.aclose()
        if proc is not None:
            logger.info(
                "stopped upstream process",
                extra={"server": self.server_id, "pid": proc.pid, "returncode": proc.returncode},
            )

    # ------------------------------------------------------------ I/O

    async def _send(self, message: JsonObject) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise UpstreamClosedError(f"server {self.server_id!r} is not running")
        # json.dumps never emits raw newlines (they are escaped inside strings).
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        async with self._write_lock:
            try:
                proc.stdin.write(data)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise UpstreamClosedError(f"server {self.server_id!r} closed its input") from exc

    async def _read_stdout(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        stream = self._proc.stdout
        splitter = LineSplitter(self._max_message_bytes)
        try:
            while chunk := await stream.read(_READ_CHUNK):
                for line, oversize in splitter.feed(chunk):
                    if oversize:
                        self.oversize_messages += 1
                        logger.warning(
                            "discarded oversize message from upstream",
                            extra={"server": self.server_id, "limit_bytes": self._max_message_bytes},
                        )
                        continue
                    self._handle_line(line)
            tail = splitter.flush()
            if tail:
                self._handle_line(tail)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stdout reader failed", extra={"server": self.server_id})
        finally:
            await self._on_stdout_closed()

    def _handle_line(self, line: bytes) -> None:
        line = line.strip()
        if not line:
            return
        try:
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.mux.stats.malformed_messages += 1
            logger.warning("non-JSON line on upstream stdout", extra={"server": self.server_id})
            return
        self.mux.handle_message(message)

    async def _read_stderr(self) -> None:
        assert self._proc is not None
        assert self._proc.stderr is not None
        stream = self._proc.stderr
        splitter = LineSplitter(self._stderr_line_bytes)
        try:
            while chunk := await stream.read(_READ_CHUNK):
                for line, truncated in splitter.feed(chunk):
                    self._record_stderr(line, truncated)
            tail = splitter.flush()
            if tail:
                self._record_stderr(tail, False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stderr reader failed", extra={"server": self.server_id})

    def _record_stderr(self, raw: bytes, truncated: bool) -> None:
        text = redactor.redact(raw.decode("utf-8", errors="replace").rstrip("\r"))
        self._stderr.append(StderrLine(time.time(), text, truncated))
        logger.debug("upstream stderr", extra={"server": self.server_id, "line": text})

    async def _on_stdout_closed(self) -> None:
        proc = self._proc
        returncode = None
        if proc is not None:
            with contextlib.suppress(Exception):
                await _wait_exit(proc, 1.0)
            returncode = proc.returncode
        self.mux.close(UpstreamClosedError(f"server {self.server_id!r} exited" + _code_suffix(returncode)))
        self._exited.set()
        if not self._stopping:
            logger.warning(
                "upstream process exited unexpectedly",
                extra={"server": self.server_id, "returncode": returncode},
            )
            if self._on_exit is not None:
                self._on_exit(self)

    # ------------------------------------------------------- upstream → us

    def _dispatch_notification(self, method: str, params: Any) -> None:
        if method == "notifications/message" and isinstance(params, dict):
            logger.info(
                "upstream log message",
                extra={
                    "server": self.server_id,
                    "upstream_level": str(params.get("level")),
                    "line": redactor.redact(_truncate(params.get("data"), 2000)),
                },
            )
        if self._on_notification is not None:
            self._on_notification(method, params)

    async def _answer_upstream_request(self, method: str, params: Any) -> Any:
        if method == "ping":
            return {}
        # Sampling, elicitation and roots are not proxied (not advertised either).
        raise UpstreamRpcError(-32601, f"method not supported by gateway: {method}")


def _resolve_executable(command: str, path: str, cwd: Any) -> str | None:
    if os.sep in command or (os.altsep and os.altsep in command):
        candidate = command if os.path.isabs(command) or cwd is None else os.path.join(str(cwd), command)
        return candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None
    return shutil.which(command, path=path)


async def _wait_exit(proc: asyncio.subprocess.Process, timeout: float) -> bool:
    if proc.returncode is not None:
        return True
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except TimeoutError:
        return False
    return True


def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, sig)
        elif proc.returncode is None:
            proc.kill() if sig == getattr(signal, "SIGKILL", None) else proc.terminate()
    except (ProcessLookupError, PermissionError):
        pass


def _code_suffix(code: int | None) -> str:
    return "" if code is None else f" with code {code}"


def _truncate(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"
