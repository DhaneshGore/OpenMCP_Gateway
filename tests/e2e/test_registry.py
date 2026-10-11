from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from openmcp_gateway.auth.backends import generate_token, hash_token
from tests.conftest import FAKE_SERVER, fake_server_config, gateway_config
from tests.e2e.harness import run_gateway
from tests.e2e.remote import serve_remote

pytestmark = pytest.mark.e2e


def setup(tmp_path: Path, **admin: Any) -> tuple[Any, dict[str, str]]:
    token = generate_token()
    cfg = gateway_config(
        [fake_server_config("static")],
        auth={"tokens": [{"name": "op", "sha256": hash_token(token), "roles": ["admin"]}]},
        admin=admin,
        storage={"backend": "sqlite", "path": str(tmp_path / "registry.db")},
    )
    return cfg, {"Authorization": f"Bearer {token}"}


def stdio_body(sid: str = "dyn") -> dict[str, Any]:
    return {"id": sid, "command": sys.executable, "args": [str(FAKE_SERVER), "normal"]}


async def test_stdio_registration_is_disabled_by_default(tmp_path: Path) -> None:
    cfg, h = setup(tmp_path)
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        resp = await http.post("/admin/servers", json=stdio_body(), headers=h)
        assert resp.status_code == 403
        assert "allow_stdio_registration" in resp.json()["detail"]


async def test_register_use_update_remove_and_persist(tmp_path: Path) -> None:
    cfg, h = setup(tmp_path, allow_stdio_registration=True)
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url, timeout=30) as http:
        created = await http.post("/admin/servers", json=stdio_body(), headers=h)
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "ready"
        assert created.json()["source"] == "registry"
        assert (await http.post("/admin/servers", json=stdio_body(), headers=h)).status_code == 409
        async with gw.client() as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"static__echo", "dyn__echo"} <= names

        bad = await http.post("/admin/servers", json={"id": "Bad Id", "command": "x"}, headers=h)
        assert bad.status_code == 422

        # Config-file servers are read-only.
        assert (await http.delete("/admin/servers/static", headers=h)).status_code == 409
        assert (
            await http.put("/admin/servers/static", json=stdio_body("static"), headers=h)
        ).status_code == 409

        updated = await http.put("/admin/servers/dyn", json={**stdio_body(), "enabled": False}, headers=h)
        assert updated.json()["state"] == "disabled"

    # Restart the gateway: the registered server comes back from SQLite.
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url, timeout=30) as http:
        servers = {s["id"]: s for s in (await http.get("/admin/servers", headers=h)).json()}
        assert servers["dyn"]["enabled"] is False
        assert (await http.delete("/admin/servers/dyn", headers=h)).status_code == 204
        assert (await http.get("/admin/servers/dyn", headers=h)).status_code == 404

    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        ids = {s["id"] for s in (await http.get("/admin/servers", headers=h)).json()}
        assert ids == {"static"}


async def test_register_remote_server_respects_egress_policy(tmp_path: Path) -> None:
    cfg, h = setup(tmp_path)
    async with serve_remote() as base:
        body = {"id": "rem", "transport": "http", "url": base + "/mcp", "allow_insecure_http": True}
        async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url, timeout=30) as http:
            created = await http.post("/admin/servers", json=body, headers=h)
            assert created.status_code == 201
            assert created.json()["state"] == "failed"
            assert "egress policy" in created.json()["last_error"]
