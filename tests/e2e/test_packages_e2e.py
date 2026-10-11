"""End-to-end: upload a multi-file package, run two versions, prove isolation."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from openmcp_gateway.auth.backends import generate_token, hash_token
from openmcp_gateway.config import GatewayConfig
from openmcp_gateway.packages.bootstrap import landlock_abi
from tests.conftest import gateway_config, require_landlock
from tests.e2e.harness import RunningGateway, run_gateway
from tests.packaging_helpers import (
    WEATHER,
    build_wheel,
    metadata,
    sha256_of,
    weather_entries,
    weather_variant,
    zip_dir,
    zip_entries,
)

pytestmark = pytest.mark.e2e

ZIP = {"Content-Type": "application/zip"}


def config(tmp_path: Path, **packages: Any) -> tuple[GatewayConfig, dict[str, str]]:
    token = generate_token()
    pkg_cfg: dict[str, Any] = {
        "enabled": True,
        "artifacts": {"path": str(tmp_path / "artifacts")},
        "catalog": {"path": str(tmp_path / "packages.db")},
        "cache_dir": str(tmp_path / "cache"),
        "runtime_dir": str(tmp_path / "runtime"),
        "sandbox": {"isolation": "landlock" if landlock_abi() >= 1 else "none"},
    }
    pkg_cfg.update(packages)
    cfg = gateway_config(
        [],
        auth={"tokens": [{"name": "ops", "sha256": hash_token(token), "roles": ["admin"]}]},
        packages=pkg_cfg,
        storage={"backend": "sqlite", "path": str(tmp_path / "registry.db")},
    )
    return cfg, {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def gateway(tmp_path: Path) -> AsyncIterator[tuple[RunningGateway, httpx.AsyncClient]]:
    cfg, headers = config(tmp_path)
    async with (
        run_gateway(cfg) as gw,
        httpx.AsyncClient(base_url=gw.base_url, headers=headers, timeout=120) as http,
    ):
        yield gw, http


async def upload(http: httpx.AsyncClient, data: bytes) -> httpx.Response:
    return await http.post("/admin/packages", content=data, headers=ZIP)


async def deploy(http: httpx.AsyncClient, version: str, server_id: str, **extra: Any) -> dict[str, Any]:
    resp = await http.post(
        f"/admin/packages/weather-mcp/{version}/deploy", json={"server_id": server_id, **extra}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["state"] == "ready", body
    return body


async def call(gw: RunningGateway, tool: str, **args: Any) -> str:
    async with gw.client() as client:
        result = await client.call_tool(tool, args)
        assert not result.is_error, result
        return result.content[0].text  # type: ignore[union-attr]


async def test_upload_run_and_use_a_multi_file_package(
    gateway: tuple[RunningGateway, httpx.AsyncClient],
) -> None:
    gw, http = gateway
    data = zip_dir(WEATHER, "weather-mcp/")

    check = await http.post("/admin/packages/validate", content=data, headers=ZIP)
    assert check.status_code == 200 and check.json()["ok"]
    assert (await http.get("/admin/packages")).json() == []  # validation stores nothing

    created = await upload(http, data)
    assert created.status_code == 201, created.text
    assert created.json()["created"] is True
    assert (await upload(http, data)).status_code == 200  # idempotent

    files = {f["path"]: f for f in (await http.get("/admin/packages/weather-mcp/1.0.0/files")).json()}
    for path in WEATHER.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            rel = path.relative_to(WEATHER).as_posix()
            assert files[rel]["sha256"] == sha256_of(path), rel

    # Registration alone runs nothing.
    assert (await http.get("/admin/servers")).json() == []

    await deploy(http, "1.0.0", "weather")
    async with gw.client() as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"weather__current", "weather__forecast", "weather__schema_title", "weather__version"} <= names
        forecast = (await client.call_tool("weather__forecast", {"city": "Oslo", "days": 2})).content[0].text  # type: ignore[union-attr]
        assert forecast.startswith("Forecast for Oslo (2 days)")  # template file + sibling modules
        assert "day 2: mean" in forecast
        current = json.loads((await client.call_tool("weather__current", {"city": "Oslo"})).content[0].text)  # type: ignore[union-attr]
        assert current["sky"] in {"clear", "cloudy", "rain", "snow"}  # YAML config file
        title = (await client.call_tool("weather__schema_title", {})).content[0].text  # type: ignore[union-attr]
        assert title == "WeatherReading"  # JSON schema file
        resource = await client.read_resource("weather://report-template")
        assert "Forecast for {city}" in resource.contents[0].text  # type: ignore[union-attr]

    where = json.loads(await call(gw, "weather__whereami"))
    assert where["cwd"] == where["root"] == where["module_root"]
    inspect = (await http.get("/admin/servers/weather")).json()
    runtime = inspect["package"]["runtime"]
    assert inspect["package"]["version"] == "1.0.0"
    assert runtime["instance_dir"] and where["root"].startswith(runtime["instance_dir"])

    # Integrity check endpoint.
    assert (await http.post("/admin/packages/weather-mcp/1.0.0/verify")).json()["ok"] is True

    # Stop removes the private runtime directory.
    instance = Path(runtime["instance_dir"])
    assert instance.exists()
    assert (await http.post("/admin/servers/weather/stop")).json()["state"] == "stopped"
    assert not instance.exists()


async def test_two_versions_run_independently(
    tmp_path: Path, gateway: tuple[RunningGateway, httpx.AsyncClient]
) -> None:
    gw, http = gateway
    assert (await upload(http, zip_dir(WEATHER))).status_code == 201
    assert (await upload(http, zip_dir(weather_variant(tmp_path, "2.0.0", marker="v2")))).status_code == 201
    await asyncio.gather(deploy(http, "1.0.0", "wx-one"), deploy(http, "2.0.0", "wx-two"))
    assert await call(gw, "wx-one__version") == "1.0.0"
    assert await call(gw, "wx-two__version") == "2.0.0"
    one = json.loads(await call(gw, "wx-one__whereami"))
    two = json.loads(await call(gw, "wx-two__whereami"))
    assert one["root"] != two["root"] and one["data"] != two["data"] and one["pid"] != two["pid"]
    # v2's extra module exists only in v2's tree.
    assert (await call(gw, "wx-two__try_read", path=f"{two['root']}/utils/marker.py")).startswith("READ:")
    assert not Path(one["root"], "utils", "marker.py").exists()
    versions = (await http.get("/admin/packages/weather-mcp")).json()
    assert {v["version"]: v["used_by"] for v in versions} == {"1.0.0": ["wx-one"], "2.0.0": ["wx-two"]}


async def test_servers_cannot_access_each_others_files(
    gateway: tuple[RunningGateway, httpx.AsyncClient], tmp_path: Path
) -> None:
    require_landlock()
    gw, http = gateway
    assert (await upload(http, zip_dir(WEATHER))).status_code == 201
    await asyncio.gather(deploy(http, "1.0.0", "alpha"), deploy(http, "1.0.0", "beta"))
    a = json.loads(await call(gw, "alpha__whereami"))
    b = json.loads(await call(gw, "beta__whereami"))

    # Each server can use its own files...
    assert (await call(gw, "alpha__try_read", path=f"{a['root']}/metadata.json")).startswith("READ:")
    assert await call(gw, "alpha__try_write", path=f"{a['data']}/note.txt") == "WROTE"
    assert await call(gw, "beta__try_write", path=f"{b['data']}/secret.txt") == "WROTE"
    # ...but not the other server's package, data or temp files,
    for path in (f"{b['root']}/metadata.json", f"{b['data']}/secret.txt"):
        assert await call(gw, "alpha__try_read", path=path) == "DENIED:PermissionError", path
    assert await call(gw, "alpha__try_write", path=f"{b['tmp']}/x") == "DENIED:PermissionError"
    # ...nor its own (read-only) package tree, the artifact store, the cache or gateway files,
    assert await call(gw, "alpha__try_write", path=f"{a['root']}/server.py") == "DENIED:PermissionError"
    for path in (
        str(tmp_path / "packages.db"),
        str(next((tmp_path / "artifacts").rglob("*.zip"))),
        str(next((tmp_path / "cache" / "artifacts").glob("*.zip"))),
        f"/proc/{b['pid']}/environ",
    ):
        assert await call(gw, "alpha__try_read", path=path) == "DENIED:PermissionError", path
    # ...nor anywhere else on the host.
    assert await call(gw, "alpha__try_write", path="/tmp/openmcp-escape-test") == "DENIED:PermissionError"
    assert not Path("/tmp/openmcp-escape-test").exists()


async def test_network_deny(gateway: tuple[RunningGateway, httpx.AsyncClient]) -> None:
    require_landlock(4, "network isolation")
    gw, http = gateway
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    try:
        assert (await upload(http, zip_dir(WEATHER))).status_code == 201
        await deploy(http, "1.0.0", "open")
        await deploy(http, "1.0.0", "closed", network="deny")
        assert await call(gw, "open__try_connect", host="127.0.0.1", port=port) == "CONNECTED"
        assert await call(gw, "closed__try_connect", host="127.0.0.1", port=port) == "DENIED:PermissionError"
    finally:
        listener.close()


async def test_corrupt_artifact_is_never_run(
    gateway: tuple[RunningGateway, httpx.AsyncClient], tmp_path: Path
) -> None:
    _gw, http = gateway
    record = (await upload(http, zip_dir(WEATHER))).json()
    stored = Path(record["artifact_uri"].removeprefix("file://"))
    os.chmod(stored, 0o644)
    data = bytearray(stored.read_bytes())
    data[300] ^= 0xFF
    stored.write_bytes(bytes(data))
    assert (await http.post("/admin/packages/weather-mcp/1.0.0/verify")).status_code == 409
    resp = await http.post("/admin/packages/weather-mcp/1.0.0/deploy", json={"server_id": "bad"})
    body = resp.json()
    assert body["state"] == "failed"
    assert "checksum mismatch" in body["error"]
    assert not list((tmp_path / "runtime").rglob("server.py"))  # nothing was extracted


async def test_upload_rejections(gateway: tuple[RunningGateway, httpx.AsyncClient], tmp_path: Path) -> None:
    _gw, http = gateway
    evil = zip_entries({**weather_entries(), "../../escape.py": "x = 1\n"})
    resp = await upload(http, evil)
    assert resp.status_code == 422
    assert any("traversal" in d for d in resp.json()["detail"]["details"])
    resp = await http.post("/admin/packages/validate", content=evil, headers=ZIP)
    assert resp.status_code == 422 and resp.json()["ok"] is False
    assert (await upload(http, zip_dir(WEATHER))).status_code == 201
    changed = weather_variant(tmp_path, "1.0.0", marker="sneaky")
    assert (await upload(http, zip_dir(changed))).status_code == 409
    assert (await upload(http, b"")).status_code == 400
    # Unknown package versions cannot be deployed or referenced.
    assert (
        await http.post("/admin/packages/weather-mcp/9.9.9/deploy", json={"server_id": "x"})
    ).status_code == 404
    bad_server = {"id": "x", "transport": "package", "package": "weather-mcp", "version": "9.9.9"}
    assert (await http.post("/admin/servers", json=bad_server)).status_code == 422


async def test_delete_respects_usage(gateway: tuple[RunningGateway, httpx.AsyncClient]) -> None:
    _gw, http = gateway
    assert (await upload(http, zip_dir(WEATHER))).status_code == 201
    await deploy(http, "1.0.0", "weather")
    assert (await http.delete("/admin/packages/weather-mcp/1.0.0")).status_code == 409
    assert (await http.delete("/admin/servers/weather")).status_code == 204
    assert (await http.delete("/admin/packages/weather-mcp/1.0.0")).status_code == 204
    assert (await http.get("/admin/packages/weather-mcp")).status_code == 404


async def test_upload_size_limit(tmp_path: Path) -> None:
    cfg, headers = config(tmp_path, limits={"max_archive_bytes": 2048})
    async with run_gateway(cfg) as gw, httpx.AsyncClient(base_url=gw.base_url, headers=headers) as http:
        resp = await http.post("/admin/packages", content=b"x" * 4096, headers=ZIP)
        assert resp.status_code == 413


async def test_packages_api_requires_admin(gateway: tuple[RunningGateway, httpx.AsyncClient]) -> None:
    gw, _ = gateway
    async with httpx.AsyncClient(base_url=gw.base_url) as anon:
        assert (await anon.post("/admin/packages", content=b"x", headers=ZIP)).status_code == 401
        assert (await anon.get("/admin/packages")).status_code == 401


async def test_locked_dependencies_are_installed_from_a_wheelhouse(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    wheel = build_wheel(wheelhouse, value="from-wheel")
    lock = f"tinydep==1.0.0 \\\n    --hash=sha256:{sha256_of(wheel)}\n"
    server = (
        (WEATHER / "server.py")
        .read_text()
        .replace(
            '@mcp.resource("weather://report-template")',
            "@mcp.tool()\ndef dependency() -> str:\n    import tinydep\n\n    return tinydep.VALUE\n\n\n"
            '@mcp.resource("weather://report-template")',
        )
    )
    entries = weather_entries(
        **{"requirements.lock": lock, "server.py": server, "metadata.json": metadata(version="3.0.0")}
    )
    cfg, headers = config(
        tmp_path, dependencies={"find_links": [str(wheelhouse)], "no_index": True, "install_timeout": 120}
    )
    async with (
        run_gateway(cfg) as gw,
        httpx.AsyncClient(base_url=gw.base_url, headers=headers, timeout=180) as http,
    ):
        record = await upload(http, zip_entries(entries))
        assert record.status_code == 201, record.text
        await deploy(http, "3.0.0", "deps")
        assert await call(gw, "deps__dependency") == "from-wheel"
        inspect = (await http.get("/admin/servers/deps")).json()
        env = inspect["package"]["runtime"]["environment"]
        assert (tmp_path / "cache" / "envs" / env / "lib").exists()

    # A lock whose hash does not match the wheel is refused at install time.
    bad_entries = weather_entries(
        **{
            "requirements.lock": f"tinydep==1.0.0 --hash=sha256:{'0' * 64}\n",
            "metadata.json": metadata(version="3.0.1"),
        }
    )
    shutil.rmtree(tmp_path / "cache" / "envs")
    async with (
        run_gateway(cfg) as gw,
        httpx.AsyncClient(base_url=gw.base_url, headers=headers, timeout=180) as http,
    ):
        assert (await upload(http, zip_entries(bad_entries))).status_code == 201
        resp = await http.post("/admin/packages/weather-mcp/3.0.1/deploy", json={"server_id": "baddeps"})
        assert resp.json()["state"] == "failed"
        assert "dependency installation failed" in resp.json()["error"]
        # The failed start left no runtime files and no half-built environment behind.
        # (The registry persisted "deps" from the first gateway, so it is running again here.)
        assert not [p for p in (tmp_path / "runtime" / "baddeps").rglob("*") if p.is_file()]
        assert not list((tmp_path / "cache" / "envs").glob(".build-*"))


async def test_crash_cleans_up_and_restarts_in_a_fresh_directory(
    gateway: tuple[RunningGateway, httpx.AsyncClient],
) -> None:
    import signal

    gw, http = gateway
    assert (await upload(http, zip_dir(WEATHER))).status_code == 201
    await deploy(http, "1.0.0", "crashy")
    first = json.loads(await call(gw, "crashy__whereami"))
    os.kill(first["pid"], signal.SIGKILL)
    for _ in range(100):
        await asyncio.sleep(0.05)
        if not Path(first["root"]).exists():
            break
    assert not Path(first["root"]).exists()  # private files removed after the crash
    for _ in range(100):  # background restart after the backoff delay
        await asyncio.sleep(0.1)
        if (await http.get("/admin/servers/crashy")).json()["state"] == "ready":
            break
    second = json.loads(await call(gw, "crashy__whereami"))
    assert second["pid"] != first["pid"] and second["root"] != first["root"]


async def test_s3_and_catalog_backends_end_to_end(
    tmp_path: Path, s3_endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Artifacts in S3 (moto); metadata in MongoDB when OPENMCP_TEST_MONGODB_URI is set, else SQLite."""
    import uuid

    from tests.conftest import MONGO_URI, make_s3

    bucket = f"pkgs-{uuid.uuid4().hex[:8]}"
    make_s3(s3_endpoint, bucket)
    monkeypatch.setenv("TEST_S3_KEY", "testing")
    monkeypatch.setenv("TEST_S3_SECRET", "testing")
    overrides: dict[str, Any] = {
        "artifacts": {
            "backend": "s3",
            "bucket": bucket,
            "endpoint_url": s3_endpoint,
            "region": "us-east-1",
            "access_key_id_env": "TEST_S3_KEY",
            "secret_access_key_env": "TEST_S3_SECRET",
        }
    }
    database = f"openmcp_e2e_{uuid.uuid4().hex[:8]}"
    if MONGO_URI:
        monkeypatch.setenv("TEST_MONGO_URI", MONGO_URI)
        overrides["catalog"] = {
            "backend": "mongodb",
            "mongodb_uri_env": "TEST_MONGO_URI",
            "database": database,
        }
    cfg, headers = config(tmp_path, **overrides)
    async with (
        run_gateway(cfg) as gw,
        httpx.AsyncClient(base_url=gw.base_url, headers=headers, timeout=120) as http,
    ):
        record = (await upload(http, zip_dir(WEATHER))).json()
        assert record["artifact_uri"].startswith(f"s3://{bucket}/packages/weather-mcp/1.0.0/")
        await deploy(http, "1.0.0", "cloud")
        assert await call(gw, "cloud__schema_title") == "WeatherReading"
        assert (await http.post("/admin/packages/weather-mcp/1.0.0/verify")).json()["ok"] is True
    if MONGO_URI:
        from pymongo import AsyncMongoClient

        client: Any = AsyncMongoClient(MONGO_URI)
        doc = await client[database]["package_versions"].find_one({"name": "weather-mcp"})
        assert doc["digest"] == record["digest"] and doc["artifact_uri"] == record["artifact_uri"]
        await client.drop_database(database)
        await client.close()


async def test_cli_package_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from openmcp_gateway.cli import main

    cfg, headers = config(tmp_path)
    monkeypatch.setenv("OPENMCP_ADMIN_TOKEN", headers["Authorization"].removeprefix("Bearer "))
    evil = tmp_path / "evil.zip"
    evil.write_bytes(zip_entries({**weather_entries(), "../escape.py": "x = 1\n"}))

    assert main(["package", "validate", str(WEATHER)]) == 0
    assert "ok: weather-mcp@1.0.0" in capsys.readouterr().out
    assert main(["package", "validate", str(evil)]) == 1
    assert "traversal" in capsys.readouterr().out

    async with run_gateway(cfg) as gw:

        def cli(*argv: str) -> int:
            return main(["package", "--url", gw.base_url, *argv])

        assert await asyncio.to_thread(cli, "upload", str(WEATHER)) == 0
        assert "weather-mcp@1.0.0: registered" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "upload", str(WEATHER)) == 0
        assert "identical content" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "upload", str(evil)) == 1
        assert "traversal" in capsys.readouterr().err
        assert await asyncio.to_thread(cli, "list") == 0
        assert "weather-mcp" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "files", "weather-mcp", "1.0.0") == 0
        assert "tools/forecast.py" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "deploy", "weather-mcp", "1.0.0", "--id", "wx") == 0
        assert "wx: ready" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "verify", "weather-mcp", "1.0.0") == 0
        assert "files verified" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "versions", "weather-mcp") == 0
        assert "wx" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "delete", "weather-mcp", "1.0.0") == 1
        assert "used by" in capsys.readouterr().err
        assert await call(gw, "wx__version") == "1.0.0"
