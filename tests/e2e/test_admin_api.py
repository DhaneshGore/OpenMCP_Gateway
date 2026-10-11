from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import MCPError

from openmcp_gateway.auth.backends import generate_token, hash_token
from openmcp_gateway.errors import RATE_LIMITED
from tests.conftest import fake_server_config, gateway_config
from tests.e2e.harness import run_gateway

pytestmark = pytest.mark.e2e


def config_with_tokens(mode: str = "none", **extra: Any) -> tuple[Any, dict[str, str]]:
    tokens = {n: generate_token() for n in ("admin", "metrics", "user")}
    cfg = gateway_config(
        [fake_server_config("f", env_from_host={"SECRET_FOR_CHILD": "OMG_TEST_SECRET"})],
        auth={
            "mode": mode,
            "tokens": [
                {"name": "admin", "sha256": hash_token(tokens["admin"]), "roles": ["admin"]},
                {
                    "name": "scraper",
                    "sha256": hash_token(tokens["metrics"]),
                    "servers": [],
                    "roles": ["metrics"],
                },
                {"name": "user", "sha256": hash_token(tokens["user"])},
            ],
        },
        **extra,
    )
    return cfg, tokens


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_admin_requires_admin_token_even_without_mcp_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMG_TEST_SECRET", "child-secret-value-123")
    cfg, tokens = config_with_tokens()
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        assert (await http.get("/admin/servers")).status_code == 401
        assert (await http.get("/admin/servers", headers=auth("nope"))).status_code == 401
        assert (await http.get("/admin/servers", headers=auth(tokens["user"]))).status_code == 403
        assert (await http.get("/admin/servers", headers=auth(tokens["metrics"]))).status_code == 403
        resp = await http.get("/admin/servers", headers=auth(tokens["admin"]))
        assert resp.status_code == 200
        assert resp.json()[0]["id"] == "f"
        assert resp.json()[0]["state"] == "ready"


async def test_inspect_never_exposes_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMG_TEST_SECRET", "child-secret-value-123")
    cfg, tokens = config_with_tokens()
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        resp = await http.get("/admin/servers/f", headers=auth(tokens["admin"]))
        assert resp.status_code == 200
        body = resp.json()
        assert "SECRET_FOR_CHILD" in body["env_names"]
        assert "child-secret-value-123" not in resp.text
        assert body["protocol_version"] == "2025-11-25"
        assert (await http.get("/admin/servers/ghost", headers=auth(tokens["admin"]))).status_code == 404


async def test_lifecycle_tools_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMG_TEST_SECRET", "child-secret-value-123")
    cfg, tokens = config_with_tokens()
    h = auth(tokens["admin"])
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        tools = (await http.get("/admin/servers/f/tools", headers=h)).json()
        assert {"name": "echo", "exposed_as": "f__echo", "description": None} in tools

        async with gw.client() as client:
            await client.call_tool("f__stderr_flood", {"lines": 3})
            await client.call_tool("f__leak_stderr", {})
        await asyncio.sleep(0.1)  # stderr is drained asynchronously
        resp = await http.get("/admin/servers/f/logs?limit=3", headers=h)
        logs = resp.json()
        assert [ln["text"][:12] for ln in logs] == ["noise line 2", "secret is [R", "Authorizatio"]
        assert "child-secret-value-123" not in resp.text
        assert "abcdefghijklmnopqrstuvwxyz" not in resp.text

        stopped = (await http.post("/admin/servers/f/stop", headers=h)).json()
        assert stopped["state"] == "stopped"
        assert (await http.get("/ready")).json()["status"] == "degraded"
        started = (await http.post("/admin/servers/f/start", headers=h)).json()
        assert started["state"] == "ready"
        restarted = (await http.post("/admin/servers/f/restart", headers=h)).json()
        assert restarted["state"] == "ready"
        assert restarted["pid"] != started["pid"]


async def test_metrics_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMG_TEST_SECRET", "child-secret-value-123")
    cfg, tokens = config_with_tokens()
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        async with gw.client() as client:
            await client.call_tool("f__echo", {"text": "x"})
            with pytest.raises(MCPError):
                await client.call_tool("f__error", {})
        resp = await http.get("/admin/metrics", headers=auth(tokens["metrics"]))
        assert resp.status_code == 200
        text = resp.text
        assert 'openmcp_mcp_requests_total{method="tools/call",outcome="ok"} 1.0' in text
        assert 'openmcp_mcp_requests_total{method="tools/call",outcome="upstream_error"} 1.0' in text
        assert 'openmcp_upstream_up{server="f"} 1.0' in text
        assert 'openmcp_upstream_requests_total{method="tools/call",outcome="ok",server="f"} 1.0' in text
        assert "child-secret-value-123" not in text
        assert (await http.get("/admin/metrics", headers=auth(tokens["user"]))).status_code == 403


async def test_admin_api_can_be_disabled() -> None:
    cfg, tokens = config_with_tokens(admin={"enabled": False})
    cfg.servers[0].env_from_host.clear()
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        assert (await http.get("/admin/servers", headers=auth(tokens["admin"]))).status_code == 404


async def test_rate_limit_is_enforced_per_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMG_TEST_SECRET", "child-secret-value-123")
    cfg, tokens = config_with_tokens(
        mode="bearer", rate_limits={"per_principal_rps": 0.01, "per_principal_burst": 3}
    )
    async with run_gateway(cfg) as gw:
        async with gw.client(tokens["user"]) as client:
            # Every MCP operation (including the tools/list the SDK client issues
            # before its first call) consumes a token; the burst is 3.
            succeeded = 0
            error: MCPError | None = None
            for _ in range(10):
                try:
                    await client.call_tool("f__echo", {"text": "x"})
                except MCPError as exc:
                    error = exc
                    break
                succeeded += 1
            assert 1 <= succeeded < 3
            assert error is not None
            assert error.code == RATE_LIMITED
            assert error.data["retryAfterSeconds"] > 0
        # Another principal has its own bucket.
        async with gw.client(tokens["admin"]) as other:
            await other.call_tool("f__echo", {"text": "x"})
