"""Package management API (`/admin/packages/*`). Requires the admin role.

Uploads are raw ZIP bodies (`Content-Type: application/zip`), read with a
hard size limit. Registration never executes a package; running one is a
separate, explicit `deploy` (or server registration) followed by `start`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from openmcp_gateway.api.admin import require_admin
from openmcp_gateway.auth.principal import Principal
from openmcp_gateway.config import PackageServerConfig
from openmcp_gateway.errors import GatewayError
from openmcp_gateway.packages.service import PackageError, PackageService
from openmcp_gateway.runtime.manager import ServerManager
from openmcp_gateway.storage.base import ServerRepository

logger = logging.getLogger("openmcp_gateway.audit")
Admin = Annotated[Principal, Depends(require_admin)]


class DirectoryRequest(BaseModel):
    path: str = Field(description="Package directory on the gateway host (trusted development only).")


class DeployRequest(BaseModel):
    server_id: str
    args: list[str] = Field(default_factory=list)
    network: str = "allow"
    env: dict[str, str] = Field(default_factory=dict)
    env_from_host: dict[str, str] = Field(default_factory=dict)
    start: bool = Field(True, description="Start immediately after creating the server.")
    autostart: bool = Field(True, description="Start with the gateway and restart after failures.")


async def read_limited_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"package archive exceeds {limit} bytes")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, f"package archive exceeds {limit} bytes")
    if not body:
        raise HTTPException(400, "request body must be a ZIP archive")
    return bytes(body)


def _error(exc: PackageError) -> HTTPException:
    detail: Any = {"error": exc.message, "details": exc.details} if exc.details else exc.message
    return HTTPException(exc.status, detail)


def build_packages_router(
    service: PackageService, manager: ServerManager, repository: ServerRepository
) -> APIRouter:
    router = APIRouter(prefix="/admin/packages", tags=["packages"])
    limit = service.config.limits.max_archive_bytes

    @router.post("/validate", summary="Validate a package archive without storing or running it")
    async def validate(request: Request, _: Admin) -> JSONResponse:
        data = await read_limited_body(request, limit)
        try:
            pkg = await service.validate(data)
        except PackageError as exc:
            return JSONResponse({"ok": False, "errors": exc.details or [exc.message]}, status_code=422)
        return JSONResponse(
            {
                "ok": True,
                "name": pkg.metadata.name,
                "version": pkg.metadata.version,
                "digest": pkg.digest,
                "size": pkg.size,
                "entrypoint": pkg.metadata.entrypoint,
                "files": [f.model_dump() for f in pkg.files],
                "has_locked_dependencies": pkg.lock_digest is not None,
                "warnings": pkg.warnings,
            }
        )

    @router.post("", summary="Upload and register a package version")
    async def upload(request: Request, principal: Admin) -> JSONResponse:
        data = await read_limited_body(request, limit)
        try:
            record, created = await service.register(data, actor=principal.name)
        except PackageError as exc:
            raise _error(exc) from None
        return JSONResponse({**record.summary(), "created": created}, status_code=201 if created else 200)

    @router.post("/register-directory", summary="Register a package from a local directory (development)")
    async def register_directory(body: DirectoryRequest, principal: Admin) -> JSONResponse:
        try:
            record, created = await service.register_directory(Path(body.path), actor=principal.name)
        except PackageError as exc:
            raise _error(exc) from None
        return JSONResponse({**record.summary(), "created": created}, status_code=201 if created else 200)

    @router.get("", summary="List packages")
    async def list_packages(_: Admin) -> list[dict[str, Any]]:
        return await service.list_packages()

    @router.get("/{name}", summary="List versions of a package")
    async def versions(name: str, _: Admin) -> list[dict[str, Any]]:
        try:
            records = await service.versions(name)
        except PackageError as exc:
            raise _error(exc) from None
        return [{**r.summary(), "used_by": manager.package_users(r.name, r.version)} for r in records]

    @router.get("/{name}/{version}", summary="Package version metadata")
    async def get_version(name: str, version: str, _: Admin) -> dict[str, Any]:
        try:
            record = await service.get(name, version)
        except PackageError as exc:
            raise _error(exc) from None
        return {
            **record.summary(),
            "metadata": record.metadata.model_dump(),
            "lock_digest": record.lock_digest,
            "used_by": manager.package_users(name, version),
        }

    @router.get("/{name}/{version}/files", summary="Files in a package version (paths, sizes, checksums)")
    async def files(name: str, version: str, _: Admin) -> list[dict[str, Any]]:
        try:
            record = await service.get(name, version)
        except PackageError as exc:
            raise _error(exc) from None
        return [f.model_dump() for f in record.files]

    @router.post("/{name}/{version}/verify", summary="Re-verify stored artifact integrity")
    async def verify(name: str, version: str, _: Admin) -> JSONResponse:
        try:
            result = await service.verify(name, version)
        except PackageError as exc:
            raise _error(exc) from None
        return JSONResponse(result, status_code=200 if result["ok"] else 409)

    @router.delete("/{name}/{version}", status_code=204, summary="Delete a package version")
    async def delete(name: str, version: str, principal: Admin) -> Response:
        try:
            await service.delete(name, version, actor=principal.name)
        except PackageError as exc:
            raise _error(exc) from None
        return Response(status_code=204)

    @router.post("/{name}/{version}/deploy", status_code=201, summary="Create a server running this version")
    async def deploy(name: str, version: str, body: DeployRequest, principal: Admin) -> dict[str, Any]:
        try:
            await service.get(name, version)
        except PackageError as exc:
            raise _error(exc) from None
        try:
            definition = PackageServerConfig(
                id=body.server_id,
                transport="package",
                package=name,
                version=version,
                args=body.args,
                network=body.network,
                env=body.env,
                env_from_host=body.env_from_host,
                autostart=body.autostart,
            )
        except ValidationError as exc:
            raise HTTPException(422, [e["msg"] for e in exc.errors()]) from None
        if manager.get(definition.id) is not None:
            raise HTTPException(409, "a server with this id already exists")
        await repository.put(definition)
        server = manager.add_server(definition, source="registry")
        logger.info(
            "admin action",
            extra={
                "principal": principal.name,
                "action": "deploy",
                "server": server.id,
                "package": f"{name}@{version}",
            },
        )
        error = None
        if body.start:
            try:
                await manager.start(server.id)
            except GatewayError as exc:
                error = exc.public_message
        return {"server_id": server.id, "state": server.state.value, "error": error}

    return router
