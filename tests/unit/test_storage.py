from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from openmcp_gateway.config import HttpServerConfig, StdioServerConfig
from openmcp_gateway.storage.base import ServerRepository
from openmcp_gateway.storage.memory import MemoryServerRepository
from openmcp_gateway.storage.sqlite import MIGRATIONS, SchemaTooNewError, SqliteServerRepository


def stdio(sid: str = "a") -> StdioServerConfig:
    return StdioServerConfig(id=sid, command="x", args=["--flag"], env_from_host={"T": "TOKEN"})


def http(sid: str = "h") -> HttpServerConfig:
    return HttpServerConfig(id=sid, transport="http", url="https://example.com/mcp")


@pytest.fixture(params=["memory", "sqlite"])
async def repo(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[ServerRepository]:
    r: ServerRepository = (
        MemoryServerRepository() if request.param == "memory" else SqliteServerRepository(tmp_path / "r.db")
    )
    yield r
    await r.close()


async def test_crud(repo: ServerRepository) -> None:
    assert await repo.list() == []
    await repo.put(stdio())
    await repo.put(http())
    assert {s.id for s in await repo.list()} == {"a", "h"}
    got = await repo.get("h")
    assert isinstance(got, HttpServerConfig)
    assert got.url == "https://example.com/mcp"
    await repo.put(stdio().model_copy(update={"args": ["--other"]}))
    a = await repo.get("a")
    assert isinstance(a, StdioServerConfig)
    assert a.args == ["--other"]
    assert await repo.delete("a") is True
    assert await repo.delete("a") is False
    assert await repo.get("a") is None


async def test_sqlite_persists_across_restarts(tmp_path: Path) -> None:
    path = tmp_path / "r.db"
    first = SqliteServerRepository(path)
    await first.put(stdio())
    await first.close()
    second = SqliteServerRepository(path)
    assert [s.id for s in await second.list()] == ["a"]
    assert second.schema_version == len(MIGRATIONS)
    await second.close()


async def test_sqlite_refuses_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "r.db"
    await SqliteServerRepository(path).close()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO schema_migrations VALUES (999, 'future')")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaTooNewError):
        SqliteServerRepository(path)


async def test_sqlite_stores_env_references_not_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOKEN", "super-secret-token-value")
    repo = SqliteServerRepository(tmp_path / "r.db")
    await repo.put(stdio())
    await repo.close()
    data = b"".join(p.read_bytes() for p in tmp_path.iterdir())
    assert b"super-secret-token-value" not in data
    assert b"TOKEN" in data


def test_unwritable_registry_path_is_a_clear_config_error(tmp_path: Path) -> None:
    from openmcp_gateway.app import build_repository
    from openmcp_gateway.config import ConfigError, GatewayConfig

    cfg = GatewayConfig(storage={"backend": "sqlite", "path": str(tmp_path / "missing-dir" / "r.db")})
    with pytest.raises(ConfigError, match="cannot open registry database"):
        build_repository(cfg)
