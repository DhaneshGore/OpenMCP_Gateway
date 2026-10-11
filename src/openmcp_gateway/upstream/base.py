"""The interface every upstream transport implements (the transport extension point)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import mcp_types as types


@dataclass(frozen=True)
class StderrLine:
    ts: float
    text: str
    truncated: bool


class UpstreamConnection(Protocol):
    server_id: str

    @property
    def protocol_version(self) -> str | None: ...

    @property
    def server_info(self) -> types.Implementation | None: ...

    @property
    def capabilities(self) -> types.ServerCapabilities | None: ...

    @property
    def instructions(self) -> str | None: ...

    @property
    def started_at(self) -> float | None: ...

    @property
    def alive(self) -> bool: ...

    @property
    def pid(self) -> int | None:
        """OS process id for local transports, else None."""
        ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def stderr(self) -> Sequence[StderrLine]:
        """Captured, redacted diagnostic output (empty for remote transports)."""
        ...

    def stats(self) -> dict[str, int]:
        """Monotonic counters for metrics; reset when the connection is replaced."""
        ...

    async def start(self) -> None:
        """Connect and complete protocol negotiation, or raise UpstreamStartError."""
        ...

    async def stop(self) -> None: ...

    async def request(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        """Send a request; return the raw JSON result or raise UpstreamRpcError / GatewayError."""
        ...
