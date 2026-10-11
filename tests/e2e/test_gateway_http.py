"""End-to-end: SDK client -> HTTP -> gateway -> real stdio subprocesses."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
import pytest
from mcp.shared.exceptions import MCPError

from openmcp_gateway.auth.backends import generate_token, hash_token
from openmcp_gateway.errors import UPSTREAM_PROTOCOL_ERROR
from openmcp_gateway.routing.namespace import META_KEY
from tests.conftest import demo_server_config, fake_server_config, gateway_config
from tests.e2e.harness import run_gateway

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_demo_server_round_trip(mode: str) -> None:
    async with run_gateway(gateway_config([demo_server_config()])) as gw, gw.client(mode=mode) as client:
        expected = "2025-11-25" if mode == "legacy" else "2026-07-28"
        assert client.protocol_version == expected
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {"demo__echo", "demo__add", "demo__sleep_then_echo"} <= set(tools)
        assert tools["demo__add"].meta is not None
        assert tools["demo__add"].meta[META_KEY] == {"server": "demo", "name": "add"}
        result = await client.call_tool("demo__add", {"a": 2, "b": 40})
        assert result.content[0].text == "42.0"  # type: ignore[union-attr]
        assert not result.is_error
        prompt = await client.get_prompt("demo__greet", {"name": "Ada"})
        assert "Ada" in prompt.messages[0].content.text  # type: ignore[union-attr]
        resource = await client.read_resource("demo://readme")
        assert "demo echo server" in resource.contents[0].text  # type: ignore[union-attr]


async def test_fifty_concurrent_calls_get_their_own_results() -> None:
    rng = random.Random(42)
    async with run_gateway(gateway_config([fake_server_config("f")])) as gw:

        async def one_client(c: int) -> list[str]:
            async with gw.client() as client:
                calls = [
                    client.call_tool("f__sleep", {"seconds": rng.uniform(0, 0.4), "text": f"c{c}-r{r}"})
                    for r in range(10)
                ]
                return [res.content[0].text for res in await asyncio.gather(*calls)]  # type: ignore[union-attr]

        results = await asyncio.gather(*(one_client(c) for c in range(5)))
        assert results == [[f"c{c}-r{r}" for r in range(10)] for c in range(5)]


async def test_two_servers_are_namespaced_and_isolated() -> None:
    cfg = gateway_config([fake_server_config("one"), fake_server_config("two")])
    async with run_gateway(cfg) as gw, gw.client() as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"one__echo", "two__echo"} <= names
        a, b = await asyncio.gather(
            client.call_tool("one__echo", {"text": "from-one"}),
            client.call_tool("two__echo", {"text": "from-two"}),
        )
        assert a.content[0].text == "from-one"  # type: ignore[union-attr]
        assert b.content[0].text == "from-two"  # type: ignore[union-attr]


async def test_error_semantics() -> None:
    cfg = gateway_config([fake_server_config("f", timeouts={"startup": 5, "request": 0.5, "shutdown": 1})])
    async with run_gateway(cfg) as gw, gw.client() as client:
        with pytest.raises(MCPError) as info:
            await client.call_tool("f__error", {})
        assert info.value.code == -32602 and info.value.message == "bad args"

        with pytest.raises(MCPError) as info:
            await client.call_tool("missing__tool", {})
        assert info.value.code == -32602

        with pytest.raises(MCPError) as info:
            await client.call_tool("f__bad_result", {})
        assert info.value.code == UPSTREAM_PROTOCOL_ERROR

        with pytest.raises(MCPError) as info:
            await client.call_tool("f__sleep", {"seconds": 3, "text": "x"})
        assert info.value.code == -32001
        assert "did not answer" in info.value.message


async def test_tool_filters_hide_and_block_tools() -> None:
    cfg = gateway_config([fake_server_config("f", tools={"allow": ["echo", "sleep"], "deny": ["sleep"]})])
    async with run_gateway(cfg) as gw, gw.client() as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert names == {"f__echo"}
        with pytest.raises(MCPError):
            await client.call_tool("f__env", {})


async def test_progress_is_relayed_and_forged_tokens_are_not() -> None:
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    async with run_gateway(gateway_config([fake_server_config("f")])) as gw, gw.client() as client:
        result = await client.call_tool("f__progress", {}, progress_callback=on_progress)
        assert result.content[0].text.startswith("token=omg-progress-")  # type: ignore[union-attr]
    assert seen == [(1.0, 3.0, "step 1"), (2.0, 3.0, "step 2"), (3.0, 3.0, "step 3")]


async def test_one_broken_server_does_not_break_listing() -> None:
    cfg = gateway_config(
        [
            fake_server_config("good"),
            fake_server_config("bad", mode="crash-on-start", restart={"policy": "never"}),
        ]
    )
    async with run_gateway(cfg) as gw, gw.client() as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert "good__echo" in names
        assert not any(n.startswith("bad__") for n in names)
        with pytest.raises(MCPError) as info:
            await client.call_tool("bad__echo", {"text": "x"})
        assert "will not be restarted" in info.value.message


async def test_health_ready_version() -> None:
    async with (
        run_gateway(gateway_config([fake_server_config("f")])) as gw,
        httpx.AsyncClient(base_url=gw.base_url) as http,
    ):
        assert (await http.get("/health")).json() == {"status": "ok"}
        ready = await http.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready", "servers": {"expected": 1, "ready": 1}}
        version = (await http.get("/version")).json()
        assert "2025-11-25" in version["protocol_versions"]["client_facing"]
        assert "x-request-id" in ready.headers


async def test_dns_rebinding_and_body_limits() -> None:
    cfg = gateway_config([fake_server_config("f")], limits={"max_request_body_bytes": 2048})
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        init: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
        evil = await http.post("/mcp", json=init, headers={**headers, "Host": "evil.example"})
        assert evil.status_code == 421
        evil_origin = await http.post("/mcp", json=init, headers={**headers, "Origin": "http://evil.example"})
        assert evil_origin.status_code == 403
        big = await http.post("/mcp", content=b"x" * 10_000, headers=headers)
        assert big.status_code == 413


# ------------------------------------------------------------------ auth


def bearer_config(**extra: Any) -> tuple[Any, dict[str, str]]:
    tokens = {name: generate_token() for name in ("alice", "bob", "admin")}
    cfg = gateway_config(
        [fake_server_config("a"), fake_server_config("b")],
        auth={
            "mode": "bearer",
            "tokens": [
                {"name": "alice", "sha256": hash_token(tokens["alice"]), "servers": ["a"]},
                {"name": "bob", "sha256": hash_token(tokens["bob"]), "servers": ["*"]},
                {"name": "admin", "sha256": hash_token(tokens["admin"]), "servers": [], "roles": ["admin"]},
            ],
        },
        **extra,
    )
    return cfg, tokens


async def test_unauthenticated_requests_are_rejected() -> None:
    cfg, _ = bearer_config()
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}):
            resp = await http.post("/mcp", json={}, headers=headers)
            assert resp.status_code == 401
            assert resp.headers["www-authenticate"].startswith("Bearer")
        # Health endpoints stay public.
        assert (await http.get("/health")).status_code == 200


async def test_principals_only_see_and_call_permitted_servers() -> None:
    cfg, tokens = bearer_config()
    async with run_gateway(cfg) as gw:
        async with gw.client(tokens["alice"]) as alice:
            names = {t.name for t in (await alice.list_tools()).tools}
            assert "a__echo" in names
            assert not any(n.startswith("b__") for n in names)
            with pytest.raises(MCPError) as info:
                await alice.call_tool("b__echo", {"text": "x"})
            # Same error as a tool that does not exist at all.
            assert info.value.code == -32602 and info.value.message == "unknown tool: b__echo"
        async with gw.client(tokens["bob"]) as bob:
            names = {t.name for t in (await bob.list_tools()).tools}
            assert {"a__echo", "b__echo"} <= names
        async with gw.client(tokens["admin"]) as admin:
            assert (await admin.list_tools()).tools == []


async def test_session_cannot_be_used_by_another_principal() -> None:
    cfg, tokens = bearer_config()
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url) as http:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
        resp = await http.post(
            "/mcp", json=init, headers={**headers, "Authorization": f"Bearer {tokens['alice']}"}
        )
        assert resp.status_code == 200
        session_id = resp.headers["mcp-session-id"]
        hijack = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={**headers, "Authorization": f"Bearer {tokens['bob']}", "Mcp-Session-Id": session_id},
        )
        assert hijack.status_code == 404
