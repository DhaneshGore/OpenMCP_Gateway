"""Egress (SSRF) protection for remote upstream connections.

The check runs at **connect time**, inside the HTTP client's network backend:
the hostname is resolved, every resolved address is validated, and the socket
is opened to a validated address. Validating at connect time (rather than once
when the URL is configured) defeats DNS rebinding, and it covers redirects
automatically because every new connection passes through the same check.
TLS still verifies the certificate against the original hostname (SNI).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpcore2
import httpx2

logger = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class EgressDeniedError(httpcore2.ConnectError):
    """Raised (as a connect error) when a destination address is not allowed."""


@dataclass(frozen=True)
class EgressPolicy:
    """`allow_private`: permit non-global addresses (trusted internal deployments).
    `allowed_networks`: specific non-global networks that are always permitted."""

    allow_private: bool = False
    allowed_networks: tuple[IPNetwork, ...] = field(default_factory=tuple)

    def is_allowed(self, address: IPAddress) -> bool:
        ip = _unwrap(address)
        if ip.is_multicast or ip.is_unspecified:
            return False
        if any(ip in net for net in self.allowed_networks):
            return True
        if self.allow_private:
            return True
        return ip.is_global


def _unwrap(ip: IPAddress) -> IPAddress:
    """Map IPv6 forms that embed an IPv4 address to that IPv4 address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip.teredo is not None:
            return ip.teredo[1]
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


async def resolve(host: str, port: int) -> list[IPAddress]:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return [literal]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    out: list[IPAddress] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if addr not in out:
            out.append(addr)
    return out


class GuardedBackend(httpcore2.AsyncNetworkBackend):
    """Network backend that only connects to policy-approved addresses."""

    def __init__(self, policy: EgressPolicy, inner: httpcore2.AsyncNetworkBackend | None = None) -> None:
        self._policy = policy
        self._inner = inner or httpcore2.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            addresses = await resolve(host, port)
        except OSError as exc:
            raise httpcore2.ConnectError(f"cannot resolve {host}") from exc
        if not addresses:
            raise httpcore2.ConnectError(f"cannot resolve {host}")
        denied = [a for a in addresses if not self._policy.is_allowed(a)]
        if denied:
            # All-or-nothing: a name that resolves to *any* internal address is refused,
            # so an attacker cannot mix public and internal records.
            logger.warning("egress denied", extra={"host": host, "address": str(denied[0])})
            raise EgressDeniedError(f"destination {host} resolves to a non-public address")
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore2.ConnectError as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore2.AsyncNetworkStream:
        raise EgressDeniedError("unix sockets are not allowed for remote upstreams")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, inner: httpx2.AsyncByteStream, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        seen = 0
        async for chunk in self._inner:
            seen += len(chunk)
            if seen > self._limit:
                raise httpx2.ReadError(f"response body exceeds {self._limit} bytes")
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class GuardedTransport(httpx2.AsyncHTTPTransport):
    """`AsyncHTTPTransport` whose connections go through `GuardedBackend`.

    JSON response bodies are capped at `max_body_bytes` (SSE streams are
    long-lived; the SDK caps individual SSE events instead).
    """

    def __init__(self, policy: EgressPolicy, *, max_body_bytes: int, verify: bool = True) -> None:
        super().__init__(trust_env=False, verify=verify)
        # httpx2 does not expose the network backend; replace the pool it built.
        # Covered by tests/integration/test_ssrf.py so an upgrade that changes
        # this internal fails loudly instead of silently bypassing the guard.
        old_pool = self._pool
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=old_pool._ssl_context,
            network_backend=GuardedBackend(policy),
        )
        self._max_body_bytes = max_body_bytes

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await super().handle_async_request(request)
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("text/event-stream"):
            response.stream = _LimitedStream(response.stream, self._max_body_bytes)  # type: ignore[arg-type]
        return response
