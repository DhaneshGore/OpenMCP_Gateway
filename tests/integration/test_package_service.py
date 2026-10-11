"""Package service with local, S3 (moto) and MongoDB backends."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from openmcp_gateway.config import PackagesConfig
from openmcp_gateway.packages.archive import sha256_hex
from openmcp_gateway.packages.artifacts import ArtifactExistsError, LocalArtifactStore
from openmcp_gateway.packages.catalog import MongoPackageCatalog, SqlitePackageCatalog, VersionExistsError
from openmcp_gateway.packages.service import (
    PackageConflictError,
    PackageForbiddenError,
    PackageIntegrityError,
    PackageInvalidError,
    PackageService,
)
from tests.conftest import MONGO_URI, make_s3
from tests.packaging_helpers import WEATHER, weather_variant, zip_dir, zip_entries

pytestmark = pytest.mark.e2e


def packages_config(tmp_path: Path, **overrides: Any) -> PackagesConfig:
    data: dict[str, Any] = {
        "enabled": True,
        "artifacts": {"path": str(tmp_path / "artifacts")},
        "catalog": {"path": str(tmp_path / "packages.db")},
        "cache_dir": str(tmp_path / "cache"),
        "runtime_dir": str(tmp_path / "runtime"),
    }
    data.update(overrides)
    return PackagesConfig(**data)


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[PackageService]:
    cfg = packages_config(tmp_path)
    svc = PackageService(cfg, LocalArtifactStore(cfg.artifacts.path), SqlitePackageCatalog(cfg.catalog.path))
    yield svc
    await svc.close()


async def test_register_is_idempotent_and_versions_are_immutable(
    service: PackageService, tmp_path: Path
) -> None:
    record, created = await service.register(zip_dir(WEATHER, "weather-mcp/"), actor="ops")
    assert created
    assert record.name == "weather-mcp" and record.version == "1.0.0"
    assert record.artifact_uri.startswith("file://")
    assert record.created_by == "ops"
    # Same content (zipped differently): same record, not a new one.
    again, created = await service.register(zip_dir(WEATHER, reverse=True), actor="ops")
    assert not created and again.digest == record.digest
    # Same version, different content: rejected.
    changed = weather_variant(tmp_path, "1.0.0", marker="changed")
    with pytest.raises(PackageConflictError, match="immutable"):
        await service.register(zip_dir(changed), actor="ops")
    # New version is fine and independent.
    v2, created = await service.register(zip_dir(weather_variant(tmp_path, "1.1.0")), actor="ops")
    assert created and v2.digest != record.digest
    assert [r.version for r in await service.versions("weather-mcp")] == ["1.0.0", "1.1.0"]
    assert await service.list_packages() == [
        {"name": "weather-mcp", "versions": ["1.0.0", "1.1.0"], "latest": "1.1.0"}
    ]


async def test_all_files_are_stored_and_restored(service: PackageService, tmp_path: Path) -> None:
    record, _ = await service.register(zip_dir(WEATHER), actor="ops")
    artifact = await service.fetch_verified(record)
    assert sha256_hex(artifact.read_bytes()) == record.digest
    expected = {
        p.relative_to(WEATHER).as_posix()
        for p in WEATHER.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert {f.path for f in record.files} == expected
    result = await service.verify("weather-mcp", "1.0.0")
    assert result["ok"] is True and result["files"] == len(expected)


async def test_corrupt_artifact_is_detected(service: PackageService) -> None:
    record, _ = await service.register(zip_dir(WEATHER), actor="ops")
    stored = Path(record.artifact_uri.removeprefix("file://"))
    os.chmod(stored, 0o644)
    data = bytearray(stored.read_bytes())
    data[200] ^= 0xFF
    stored.write_bytes(bytes(data))
    with pytest.raises(PackageIntegrityError, match="checksum mismatch"):
        await service.fetch_verified(record)
    report = await service.verify("weather-mcp", "1.0.0")
    assert report["ok"] is False and "mismatch" in report["error"]


async def test_cached_artifact_is_reverified(service: PackageService) -> None:
    record, _ = await service.register(zip_dir(WEATHER), actor="ops")
    cached = await service.fetch_verified(record)
    cached.write_bytes(b"tampered cache")
    again = await service.fetch_verified(record)  # re-downloaded from the store
    assert sha256_hex(again.read_bytes()) == record.digest


async def test_invalid_upload(service: PackageService) -> None:
    with pytest.raises(PackageInvalidError) as info:
        await service.register(zip_entries({"../x.py": "x"}), actor="ops")
    assert any("traversal" in d for d in info.value.details)


async def test_delete_policies(tmp_path: Path) -> None:
    users: dict[tuple[str, str], list[str]] = {}
    cfg = packages_config(tmp_path, retention={"keep_latest": 1})
    svc = PackageService(
        cfg,
        LocalArtifactStore(cfg.artifacts.path),
        SqlitePackageCatalog(cfg.catalog.path),
        usage=lambda n, v: users.get((n, v), []),
    )
    try:
        await svc.register(zip_dir(WEATHER), actor="ops")
        await svc.register(zip_dir(weather_variant(tmp_path, "1.1.0")), actor="ops")
        with pytest.raises(PackageForbiddenError, match="retained"):
            await svc.delete("weather-mcp", "1.1.0", actor="ops")
        users[("weather-mcp", "1.0.0")] = ["prod"]
        with pytest.raises(PackageConflictError, match="prod"):
            await svc.delete("weather-mcp", "1.0.0", actor="ops")
        users.clear()
        await svc.delete("weather-mcp", "1.0.0", actor="ops")
        assert [r.version for r in await svc.versions("weather-mcp")] == ["1.1.0"]
        assert not list((tmp_path / "artifacts" / "weather-mcp" / "1.0.0").glob("*.zip"))
    finally:
        await svc.close()


async def test_directory_registration_requires_opt_in_and_root(tmp_path: Path) -> None:
    cfg = packages_config(tmp_path)
    svc = PackageService(cfg, LocalArtifactStore(cfg.artifacts.path), SqlitePackageCatalog(cfg.catalog.path))
    with pytest.raises(PackageForbiddenError, match="disabled"):
        await svc.register_directory(WEATHER, actor="dev")
    await svc.close()
    cfg = packages_config(
        tmp_path, allow_directory_registration=True, directory_roots=[str(tmp_path / "elsewhere")]
    )
    svc = PackageService(cfg, LocalArtifactStore(cfg.artifacts.path), SqlitePackageCatalog(cfg.catalog.path))
    with pytest.raises(PackageForbiddenError, match="directory_roots"):
        await svc.register_directory(WEATHER, actor="dev")
    await svc.close()
    cfg = packages_config(tmp_path, allow_directory_registration=True, directory_roots=[str(WEATHER.parent)])
    svc = PackageService(cfg, LocalArtifactStore(cfg.artifacts.path), SqlitePackageCatalog(cfg.catalog.path))
    record, created = await svc.register_directory(WEATHER, actor="dev")
    assert created and record.version == "1.0.0"
    await svc.close()


async def test_local_store_is_write_once(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "a")
    await store.put("p/1.0.0/abc.zip", b"one")
    with pytest.raises(ArtifactExistsError):
        await store.put("p/1.0.0/abc.zip", b"two")
    with pytest.raises(ValueError, match="invalid artifact key"):
        await store.put("../../escape.zip", b"x")


# ---------------------------------------------------------------- S3 (moto)


async def test_s3_store_roundtrip_and_immutability(s3_endpoint: str, tmp_path: Path) -> None:
    store = make_s3(s3_endpoint, f"b-{uuid.uuid4().hex[:8]}")
    uri = await store.put("p/1.0.0/abc.zip", b"payload")
    assert uri.startswith("s3://") and uri.endswith("packages/p/1.0.0/abc.zip")
    with pytest.raises(ArtifactExistsError):
        await store.put("p/1.0.0/abc.zip", b"other")
    dest = tmp_path / "out.zip"
    await store.download("p/1.0.0/abc.zip", dest)
    assert dest.read_bytes() == b"payload"
    await store.delete("p/1.0.0/abc.zip")
    from openmcp_gateway.packages.artifacts import ArtifactNotFoundError

    with pytest.raises(ArtifactNotFoundError):
        await store.download("p/1.0.0/abc.zip", dest)
    await store.close()


async def test_service_with_s3_backend(s3_endpoint: str, tmp_path: Path) -> None:
    cfg = packages_config(tmp_path)
    store = make_s3(s3_endpoint, f"b-{uuid.uuid4().hex[:8]}")
    svc = PackageService(cfg, store, SqlitePackageCatalog(cfg.catalog.path))
    record, created = await svc.register(zip_dir(WEATHER), actor="ops")
    assert created and record.artifact_uri.startswith(f"s3://{store.bucket}/packages/weather-mcp/1.0.0/")
    artifact = await svc.fetch_verified(record)
    assert sha256_hex(artifact.read_bytes()) == record.digest
    assert (await svc.verify("weather-mcp", "1.0.0"))["ok"]
    await svc.close()


# ---------------------------------------------------------------- MongoDB


@pytest.mark.services
@pytest.mark.skipif(not MONGO_URI, reason="set OPENMCP_TEST_MONGODB_URI to run MongoDB tests")
async def test_mongodb_catalog(tmp_path: Path) -> None:
    assert MONGO_URI is not None
    catalog = MongoPackageCatalog(MONGO_URI, f"openmcp_test_{uuid.uuid4().hex[:8]}", "package_versions")
    cfg = packages_config(tmp_path)
    svc = PackageService(cfg, LocalArtifactStore(cfg.artifacts.path), catalog)
    try:
        record, created = await svc.register(zip_dir(WEATHER), actor="ops")
        assert created
        got = await svc.get("weather-mcp", "1.0.0")
        assert got.digest == record.digest and got.files == record.files
        with pytest.raises(VersionExistsError):
            await catalog.insert(record)
        await svc.register(zip_dir(weather_variant(tmp_path, "1.1.0")), actor="ops")
        assert [r.version for r in await svc.versions("weather-mcp")] == ["1.0.0", "1.1.0"]
        assert await catalog.names() == ["weather-mcp"]
        await svc.delete("weather-mcp", "1.0.0", actor="ops")
        assert await catalog.get("weather-mcp", "1.0.0") is None
    finally:
        await catalog._client.drop_database(catalog._coll.database.name)
        await svc.close()
