"""Unauthenticated liveness, readiness and version endpoints.

They reveal only aggregate counts and versions: no server ids, commands,
environment, errors or tool names (those are behind authentication).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

from openmcp_gateway import __version__
from openmcp_gateway.runtime.manager import ServerState

router = APIRouter(tags=["health"])


def _sdk_version() -> str | None:
    try:
        return version("mcp")
    except PackageNotFoundError:  # pragma: no cover
        return None


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    servers = state.manager.all()
    expected = [s for s in servers if s.config.enabled and s.config.autostart]
    ready_count = sum(1 for s in expected if s.state is ServerState.READY)
    all_ready = ready_count == len(expected)
    is_ready = bool(state.started) and (all_ready or not state.config.gateway.ready_requires_all_servers)
    # Counts only: server ids are visible to authorized callers and admins, not to anonymous probes.
    body: dict[str, Any] = {
        "status": "ready" if is_ready else "not_ready",
        "servers": {"expected": len(expected), "ready": ready_count},
    }
    if is_ready and not all_ready:
        body["status"] = "degraded"
    return JSONResponse(body, status_code=200 if is_ready else 503)


@router.get("/version", summary="Gateway and protocol versions")
async def version_info(request: Request) -> dict[str, Any]:
    return {
        "name": request.app.state.config.gateway.name,
        "version": __version__,
        "mcp_sdk_version": _sdk_version(),
        "protocol_versions": {
            "client_facing": [*HANDSHAKE_PROTOCOL_VERSIONS, *MODERN_PROTOCOL_VERSIONS],
            "upstream_stdio": list(HANDSHAKE_PROTOCOL_VERSIONS),
        },
    }
