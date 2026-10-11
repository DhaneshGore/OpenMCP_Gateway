"""Pure-ASGI middleware (safe for streaming SSE responses)."""

from __future__ import annotations

import json
import re
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openmcp_gateway.auth.backends import GatewayUser
from openmcp_gateway.logconfig import request_id_var

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIdMiddleware:
    """Accepts a well-formed X-Request-ID or generates one; echoes it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for key, value in scope.get("headers", []):
            if key == b"x-request-id":
                candidate = value.decode("latin-1")
                if _VALID_REQUEST_ID.match(candidate):
                    incoming = candidate
                break
        rid = incoming or uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class RequirePrincipal:
    """Wraps an ASGI app; answers 401 unless authentication produced a GatewayUser."""

    def __init__(self, app: ASGIApp, realm: str) -> None:
        self.app = app
        self.realm = realm

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not isinstance(scope.get("user"), GatewayUser):
            body = json.dumps({"error": "unauthorized", "detail": "valid bearer token required"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", f'Bearer realm="{self.realm}"'.encode()),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
