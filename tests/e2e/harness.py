"""Run the real gateway (uvicorn + FastAPI + subprocesses) inside a test."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from openmcp_gateway.app import create_app
from openmcp_gateway.config import GatewayConfig


@dataclass
class RunningGateway:
    base_url: str
    app: Any

    @property
    def mcp_url(self) -> str:
        return self.base_url + self.app.state.config.gateway.mcp_path

    def client(self, token: str | None = None, mode: str = "legacy", **kw: Any) -> Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        http = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30.0))
        return Client(streamable_http_client(self.mcp_url, http_client=http), mode=mode, **kw)  # type: ignore[arg-type]


@contextlib.asynccontextmanager
async def run_gateway(config: GatewayConfig) -> AsyncIterator[RunningGateway]:
    app = create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, lifespan="on", ws="none")
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started or task.done():
                break
            await asyncio.sleep(0.02)
        if task.done():
            task.result()
            raise RuntimeError("gateway exited during startup")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield RunningGateway(base_url=f"http://127.0.0.1:{port}", app=app)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 30)
