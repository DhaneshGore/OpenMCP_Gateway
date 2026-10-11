"""Transport-agnostic JSON-RPC 2.0 request multiplexer.

One instance serves one upstream connection. Callers issue requests
concurrently; each gets a fresh integer id and a future. Inbound messages are
fed to `handle_message` by the transport's reader task, and a response resolves
**only** the future registered under exactly its id. See ARCHITECTURE.md §4 for
the full list of rules this module implements.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openmcp_gateway.errors import (
    OverloadedError,
    UpstreamClosedError,
    UpstreamRpcError,
    UpstreamTimeoutError,
)

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]
SendFn = Callable[[JsonObject], Awaitable[None]]
NotificationHandler = Callable[[str, Any], None]
# Returns the `result` for an upstream->gateway request, or raises UpstreamRpcError.
RequestHandler = Callable[[str, Any], Awaitable[Any]]

METHOD_NOT_FOUND = -32601

# Requests that must never be followed by notifications/cancelled (spec).
_UNCANCELLABLE = frozenset({"initialize"})


@dataclass
class RpcStats:
    requests_sent: int = 0
    responses_matched: int = 0
    responses_unmatched: int = 0  # late (after timeout/cancel), duplicate or unknown id
    malformed_messages: int = 0
    notifications_received: int = 0
    requests_received: int = 0
    timeouts: int = 0
    cancellations: int = 0
    rejected_overload: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class _Pending:
    method: str
    future: asyncio.Future[Any]
    sent: bool = False


@dataclass
class JsonRpcMultiplexer:
    send: SendFn
    max_pending: int = 64
    name: str = "upstream"
    on_notification: NotificationHandler | None = None
    on_request: RequestHandler | None = None
    stats: RpcStats = field(default_factory=RpcStats)

    def __post_init__(self) -> None:
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._closed: BaseException | None = None
        self._background: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ API

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def closed(self) -> bool:
        return self._closed is not None

    async def request(self, method: str, params: Any = None, *, timeout: float) -> Any:
        """Send a request and wait for its result.

        Raises UpstreamRpcError (upstream returned an error), UpstreamTimeoutError,
        UpstreamClosedError, OverloadedError; propagates CancelledError after
        cleaning up.
        """
        if self._closed is not None:
            raise UpstreamClosedError(f"server {self.name!r} is not connected")
        if len(self._pending) >= self.max_pending:
            self.stats.rejected_overload += 1
            raise OverloadedError(f"too many in-flight requests to server {self.name!r}")

        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        entry = _Pending(method=method, future=loop.create_future())
        self._pending[request_id] = entry

        message: JsonObject = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params

        try:
            async with asyncio.timeout(timeout):
                await self.send(message)
                entry.sent = True
                self.stats.requests_sent += 1
                return await entry.future
        except TimeoutError:
            self.stats.timeouts += 1
            self._cancel_upstream(request_id, entry, "timeout")
            raise UpstreamTimeoutError(
                f"server {self.name!r} did not answer {method} within {timeout:g}s"
            ) from None
        except asyncio.CancelledError:
            self.stats.cancellations += 1
            self._cancel_upstream(request_id, entry, "cancelled by client")
            raise
        finally:
            self._pending.pop(request_id, None)
            if not entry.future.done():
                entry.future.cancel()

    async def notify(self, method: str, params: Any = None) -> None:
        if self._closed is not None:
            raise UpstreamClosedError(f"server {self.name!r} is not connected")
        message: JsonObject = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self.send(message)

    def close(self, exc: BaseException) -> None:
        """Fail every pending request with `exc`; further requests fail fast."""
        if self._closed is None:
            self._closed = exc
        for entry in list(self._pending.values()):
            if not entry.future.done():
                entry.future.set_exception(exc)
        self._pending.clear()

    async def aclose(self) -> None:
        """Wait for background sends (cancel notices, responses) to finish."""
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------ inbound

    def handle_message(self, message: Any) -> None:
        """Dispatch one decoded inbound JSON value (object or batch array)."""
        if isinstance(message, list):
            if not message:
                self._malformed("empty batch")
            for item in message:
                self._handle_one(item)
            return
        self._handle_one(message)

    def _handle_one(self, msg: Any) -> None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._malformed("not a JSON-RPC 2.0 object")
            return

        method = msg.get("method")
        if method is not None:
            if not isinstance(method, str):
                self._malformed("method is not a string")
                return
            if "id" in msg:
                self._handle_request(msg["id"], method, msg.get("params"))
            else:
                self.stats.notifications_received += 1
                if self.on_notification is not None:
                    try:
                        self.on_notification(method, msg.get("params"))
                    except Exception:
                        logger.exception("notification handler failed", extra={"server": self.name})
            return

        if "id" in msg and ("result" in msg) != ("error" in msg):
            self._handle_response(msg)
            return

        self._malformed("neither request, notification nor response")

    def _handle_response(self, msg: JsonObject) -> None:
        response_id = msg["id"]
        # We only ever send integer ids; bool is an int subclass and is rejected.
        if type(response_id) is not int:
            if response_id is None and "error" in msg:
                logger.warning(
                    "upstream reported an error it could not attribute to a request",
                    extra={"server": self.name, "error": _short(msg.get("error"))},
                )
            self.stats.responses_unmatched += 1
            return

        entry = self._pending.get(response_id)
        if entry is None or entry.future.done():
            self.stats.responses_unmatched += 1
            logger.debug("dropping unmatched response", extra={"server": self.name, "rpc_id": response_id})
            return

        if "error" in msg:
            err = msg["error"]
            if (
                not isinstance(err, dict)
                or type(err.get("code")) is not int
                or not isinstance(err.get("message"), str)
            ):
                entry.future.set_exception(
                    UpstreamRpcError(-32603, "upstream returned a malformed error object")
                )
                self.stats.malformed_messages += 1
                return
            entry.future.set_exception(UpstreamRpcError(err["code"], err["message"], err.get("data")))
        else:
            entry.future.set_result(msg["result"])
        self.stats.responses_matched += 1

    def _handle_request(self, request_id: Any, method: str, params: Any) -> None:
        self.stats.requests_received += 1
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            self._malformed("request id must be a string or integer")
            return
        self._spawn(self._answer_request(request_id, method, params))

    async def _answer_request(self, request_id: str | int, method: str, params: Any) -> None:
        response: JsonObject = {"jsonrpc": "2.0", "id": request_id}
        try:
            if self.on_request is None:
                raise UpstreamRpcError(METHOD_NOT_FOUND, f"method not supported by gateway: {method}")
            response["result"] = await self.on_request(method, params)
        except UpstreamRpcError as exc:
            response["error"] = {"code": exc.code, "message": exc.message}
        except Exception:
            logger.exception("upstream request handler failed", extra={"server": self.name})
            response["error"] = {"code": -32603, "message": "internal gateway error"}
        if self._closed is None:
            try:
                await self.send(response)
            except Exception:
                logger.debug("could not answer upstream request", extra={"server": self.name})

    # ------------------------------------------------------------ helpers

    def _cancel_upstream(self, request_id: int, entry: _Pending, reason: str) -> None:
        if not entry.sent or entry.method in _UNCANCELLABLE or self._closed is not None:
            return
        # Fire-and-forget: the calling task may itself be cancelled, and under
        # anyio cancel scopes any further await in it would be cancelled too.
        self._spawn(
            self._send_quietly(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": request_id, "reason": reason},
                }
            )
        )

    async def _send_quietly(self, message: JsonObject) -> None:
        try:
            await self.send(message)
        except Exception:
            logger.debug("failed to send cancellation notice", extra={"server": self.name})

    def _spawn(self, coro: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _malformed(self, why: str) -> None:
        self.stats.malformed_messages += 1
        logger.warning("malformed message from upstream: %s", why, extra={"server": self.name})


def _short(value: Any, limit: int = 300) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"
