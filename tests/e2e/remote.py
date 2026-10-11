"""A real Streamable HTTP MCP server (SDK MCPServer) for remote-upstream tests."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route


def make_remote_server(required_header: str | None = None) -> MCPServer:
    server = MCPServer("remote-test")

    @server.tool()
    def shout(text: str) -> str:
        """Upper-case the text."""
        return text.upper()

    @server.tool()
    async def slow(seconds: float) -> str:
        """Sleep, then answer."""
        await asyncio.sleep(seconds)
        return "done"

    @server.tool()
    async def report(ctx: Context) -> str:
        """Report progress three times."""
        for i in range(1, 4):
            await ctx.report_progress(i, 3, f"remote step {i}")
        return "reported"

    return server


@contextlib.asynccontextmanager
async def serve_remote(
    redirect_to: str | None = None, require_auth: str | None = None, tls: tuple[str, str] | None = None
) -> AsyncIterator[str]:
    """Yield the base URL of a running remote MCP server on 127.0.0.1."""
    inner = make_remote_server().streamable_http_app()

    async def redirect(request: Request) -> RedirectResponse:
        assert redirect_to is not None
        return RedirectResponse(redirect_to, status_code=307)

    async def guarded(scope, receive, send):  # type: ignore[no-untyped-def]
        if require_auth and scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            if headers.get(b"authorization", b"").decode() != f"Bearer {require_auth}":
                from starlette.responses import Response

                await Response("unauthorized", status_code=401)(scope, receive, send)
                return
        await inner(scope, receive, send)

    routes = [Route("/redirect/mcp", redirect, methods=["GET", "POST"])] if redirect_to else []
    app = Starlette(
        routes=[*routes, Mount("/", app=guarded)], lifespan=lambda _app: inner.router.lifespan_context(inner)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_config=None,
            lifespan="on",
            ws="none",
            **({"ssl_certfile": tls[0], "ssl_keyfile": tls[1]} if tls else {}),
        )
    )
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"{'https' if tls else 'http'}://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
