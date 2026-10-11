"""Gateway error taxonomy.

Every error that can reach an MCP client is one of these classes, so the text a
client sees is always written by the gateway — never a raw exception message or
upstream stderr. JSON-RPC codes in the implementation-defined server-error
range (-32000..-32099) are documented in docs/troubleshooting.md.
"""

from __future__ import annotations

from typing import Any

from mcp_types import CONNECTION_CLOSED, INTERNAL_ERROR, INVALID_PARAMS, REQUEST_TIMEOUT

UPSTREAM_UNAVAILABLE = -32010
OVERLOADED = -32011
RATE_LIMITED = -32012
UPSTREAM_PROTOCOL_ERROR = -32013


class GatewayError(Exception):
    """Base class. `public_message` is safe to show to clients."""

    code: int = INTERNAL_ERROR

    def __init__(self, public_message: str, *, data: Any = None) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.data = data


class UnknownToolError(GatewayError):
    """Tool does not exist *or* the caller may not see it (deliberately indistinguishable)."""

    code = INVALID_PARAMS


class UnknownTargetError(GatewayError):
    """Prompt/resource does not exist or is not visible to the caller."""

    code = INVALID_PARAMS


class UpstreamUnavailableError(GatewayError):
    code = UPSTREAM_UNAVAILABLE


class UpstreamTimeoutError(GatewayError):
    code = REQUEST_TIMEOUT


class UpstreamClosedError(GatewayError):
    code = CONNECTION_CLOSED


class UpstreamProtocolError(GatewayError):
    code = UPSTREAM_PROTOCOL_ERROR


class OverloadedError(GatewayError):
    code = OVERLOADED


class RateLimitedError(GatewayError):
    code = RATE_LIMITED


class UpstreamStartError(UpstreamUnavailableError):
    """A server could not be started. Message is sanitized (no env values)."""


class UpstreamRpcError(Exception):
    """The upstream answered with a JSON-RPC error; forwarded to the client as-is."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"upstream error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data
