"""Remote Streamable HTTP upstreams and the SSRF guard."""

from __future__ import annotations

import ipaddress
from typing import Any

import httpcore2
import httpx2
import pytest

from openmcp_gateway.config import HttpServerConfig
from openmcp_gateway.errors import UpstreamStartError
from openmcp_gateway.security import ssrf
from openmcp_gateway.security.ssrf import EgressDeniedError, EgressPolicy, GuardedBackend, GuardedTransport
from openmcp_gateway.upstream.http import HttpUpstream
from tests.conftest import gateway_config
from tests.e2e.harness import run_gateway
from tests.e2e.remote import serve_remote

pytestmark = pytest.mark.e2e


def http_config(url: str, **kw: Any) -> HttpServerConfig:
    data: dict[str, Any] = {
        "id": "remote",
        "transport": "http",
        "url": url,
        "allow_insecure_http": True,
        "timeouts": {"startup": 5, "request": 5, "shutdown": 2},
    }
    data.update(kw)
    return HttpServerConfig(**data)


def make(cfg: HttpServerConfig, **kw: Any) -> HttpUpstream:
    return HttpUpstream(cfg, policy=EgressPolicy(), max_message_bytes=1_000_000, **kw)


async def test_guarded_transport_really_uses_the_guard() -> None:
    # Guards against an httpx2 upgrade silently dropping our network backend.
    transport = GuardedTransport(EgressPolicy(), max_body_bytes=1000)
    assert isinstance(transport._pool._network_backend, GuardedBackend)


async def test_loopback_upstream_is_refused_by_default() -> None:
    async with serve_remote() as base:
        up = make(http_config(base + "/mcp"))
        with pytest.raises(UpstreamStartError, match="egress policy"):
            await up.start()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_trusted_private_upstream_works(mode: str) -> None:
    async with serve_remote() as base:
        up = make(http_config(base + "/mcp", allow_private_network=True, protocol=mode))
        await up.start()
        try:
            assert up.protocol_version == ("2026-07-28" if mode == "auto" else "2025-11-25")
            tools = await up.request("tools/list")
            assert {t["name"] for t in tools["tools"]} >= {"shout", "slow", "report"}
            result = await up.request("tools/call", {"name": "shout", "arguments": {"text": "hi"}})
            assert result["content"][0]["text"] == "HI"
        finally:
            await up.stop()


async def test_allowed_networks_permit_specific_ranges() -> None:
    async with serve_remote() as base:
        up = HttpUpstream(
            http_config(base + "/mcp"),
            policy=EgressPolicy(allowed_networks=(ipaddress.ip_network("127.0.0.1/32"),)),
            max_message_bytes=1_000_000,
        )
        await up.start()
        await up.stop()


class RecordingBackend(httpcore2.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.dialed: list[str] = []

    async def connect_tcp(self, host: str, port: int, **kw: Any) -> httpcore2.AsyncNetworkStream:
        self.dialed.append(host)
        return httpcore2.AsyncMockStream([])


async def test_dns_rebinding_is_caught_at_connect_time(monkeypatch: pytest.MonkeyPatch) -> None:
    lookups: list[str] = []

    async def fake_resolve(host: str, port: int) -> list[Any]:
        lookups.append(host)
        # First lookup looks public; the attacker then flips the record.
        return [ipaddress.ip_address("93.184.215.14" if len(lookups) == 1 else "169.254.169.254")]

    monkeypatch.setattr(ssrf, "resolve", fake_resolve)
    inner = RecordingBackend()
    backend = GuardedBackend(EgressPolicy(), inner=inner)
    await backend.connect_tcp("rebind.example", 443)
    # The guard dials the address it validated, never the hostname.
    assert inner.dialed == ["93.184.215.14"]
    with pytest.raises(EgressDeniedError):
        await backend.connect_tcp("rebind.example", 443)
    assert inner.dialed == ["93.184.215.14"]


async def test_mixed_public_and_private_records_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str, port: int) -> list[Any]:
        return [ipaddress.ip_address("93.184.215.14"), ipaddress.ip_address("10.0.0.1")]

    monkeypatch.setattr(ssrf, "resolve", fake_resolve)
    with pytest.raises(EgressDeniedError):
        await GuardedBackend(EgressPolicy()).connect_tcp("mixed.example", 443)


async def test_redirects_to_internal_targets_are_blocked() -> None:
    # Even a client that follows redirects cannot reach a blocked address,
    # because every new connection is validated.
    transport = GuardedTransport(
        EgressPolicy(allowed_networks=(ipaddress.ip_network("127.0.0.1/32"),)), max_body_bytes=10_000
    )
    async with (
        serve_remote(redirect_to="http://10.255.255.1/latest/meta-data") as base,
        httpx2.AsyncClient(transport=transport, follow_redirects=True) as http,
    ):
        with pytest.raises(httpx2.ConnectError):
            await http.post(base + "/redirect/mcp", json={})


async def test_json_response_size_cap() -> None:
    transport = GuardedTransport(EgressPolicy(allow_private=True), max_body_bytes=10)
    async with serve_remote() as base, httpx2.AsyncClient(transport=transport) as http:
        with pytest.raises(httpx2.ReadError, match="exceeds"):
            await http.get(base + "/mcp")


async def test_auth_header_from_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", "Bearer s3cret-remote-token")
    async with serve_remote(require_auth="s3cret-remote-token") as base:
        missing = make(http_config(base + "/mcp", allow_private_network=True))
        with pytest.raises(UpstreamStartError):
            await missing.start()
        up = make(
            http_config(
                base + "/mcp", allow_private_network=True, headers_from_host={"Authorization": "REMOTE_TOKEN"}
            )
        )
        await up.start()
        await up.stop()


async def test_missing_env_for_header_is_reported_by_name() -> None:
    up = make(http_config("http://127.0.0.1:9/mcp", headers_from_host={"Authorization": "NOPE_NOT_SET"}))
    with pytest.raises(UpstreamStartError, match="NOPE_NOT_SET"):
        await up.start()


async def test_timeout_on_remote_call() -> None:
    async with serve_remote() as base:
        up = make(http_config(base + "/mcp", allow_private_network=True))
        await up.start()
        try:
            from openmcp_gateway.errors import UpstreamTimeoutError

            with pytest.raises(UpstreamTimeoutError):
                await up.request("tools/call", {"name": "slow", "arguments": {"seconds": 2}}, timeout=0.3)
            # The connection stays usable afterwards.
            ok = await up.request("tools/call", {"name": "shout", "arguments": {"text": "x"}})
            assert ok["content"][0]["text"] == "X"
        finally:
            await up.stop()


async def test_gateway_aggregates_remote_and_relays_progress() -> None:
    seen: list[float] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append(progress)

    async with serve_remote() as base:
        cfg = gateway_config([http_config(base + "/mcp", allow_private_network=True)])
        async with run_gateway(cfg) as gw, gw.client() as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert {"remote__shout", "remote__slow"} <= names
            res = await client.call_tool("remote__shout", {"text": "chained"})
            assert res.content[0].text == "CHAINED"  # type: ignore[union-attr]
            res = await client.call_tool("remote__report", {}, progress_callback=on_progress)
            assert res.content[0].text == "reported"  # type: ignore[union-attr]
            # MCPServer reports unknown tools as a tool error result; the gateway forwards it as-is.
            missing = await client.call_tool("remote__nonexistent", {})
            assert missing.is_error
    assert seen == [1.0, 2.0, 3.0]


def test_url_validation() -> None:
    with pytest.raises(ValueError, match="credentials"):
        http_config("https://user:pw@example.com/mcp")
    with pytest.raises(ValueError, match="allow_insecure_http"):
        HttpServerConfig(id="x", transport="http", url="http://example.com/mcp")
    with pytest.raises(ValueError, match="managed by the gateway"):
        http_config("https://example.com/mcp", headers={"Mcp-Session-Id": "x"})
    with pytest.raises(ValueError, match="invalid characters"):
        http_config("https://example.com/mcp", headers={"X-A": "a\r\nInjected: 1"})
    with pytest.raises(ValueError, match="http or https"):
        http_config("file:///etc/passwd")


def self_signed_cert(tmp_path: Any) -> tuple[str, str]:
    import datetime
    import ipaddress as ip

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ip.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return str(cert_path), str(key_path)


async def test_https_upstream_verifies_certificates(tmp_path: Any) -> None:
    async with serve_remote(tls=self_signed_cert(tmp_path)) as base:
        assert base.startswith("https://")
        # A certificate from an untrusted issuer is refused when verification is on (the default).
        strict = make(http_config(base + "/mcp", allow_private_network=True, allow_insecure_http=False))
        with pytest.raises(UpstreamStartError, match="connection failed"):
            await strict.start()
        # With verification explicitly disabled, the TLS connection works end to end.
        lax = make(
            http_config(
                base + "/mcp", allow_private_network=True, allow_insecure_http=False, verify_tls=False
            )
        )
        await lax.start()
        try:
            result = await lax.request("tools/call", {"name": "shout", "arguments": {"text": "tls"}})
            assert result["content"][0]["text"] == "TLS"
        finally:
            await lax.stop()
