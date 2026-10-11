"""Package management: validate, register, inspect, verify, delete, fetch."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openmcp_gateway.config import PackagesConfig
from openmcp_gateway.packages.archive import (
    IntegrityError,
    PackagePolicy,
    PackageValidationError,
    ValidatedPackage,
    archive_directory,
    extract_verified,
    inspect_archive,
    policy_from_config,
    sha256_file,
)
from openmcp_gateway.packages.artifacts import (
    ArtifactExistsError,
    ArtifactNotFoundError,
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
    artifact_key,
)
from openmcp_gateway.packages.catalog import (
    MongoPackageCatalog,
    PackageCatalog,
    SqlitePackageCatalog,
    VersionExistsError,
)
from openmcp_gateway.packages.metadata import PackageRecord

logger = logging.getLogger("openmcp_gateway.packages")
audit = logging.getLogger("openmcp_gateway.audit")


class PackageError(Exception):
    """Base class; `status` maps onto an HTTP status for the admin API."""

    status = 400

    def __init__(self, message: str, *, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


class PackageNotFoundError(PackageError):
    status = 404


class PackageConflictError(PackageError):
    status = 409


class PackageForbiddenError(PackageError):
    status = 403


class PackageInvalidError(PackageError):
    status = 422


class PackageIntegrityError(PackageError):
    status = 502


# Returns the ids of servers that reference (name, version).
UsageLookup = Callable[[str, str], list[str]]


class PackageService:
    def __init__(
        self,
        config: PackagesConfig,
        store: ArtifactStore,
        catalog: PackageCatalog,
        *,
        usage: UsageLookup | None = None,
    ) -> None:
        self.config = config
        self.policy: PackagePolicy = policy_from_config(config)
        self.store = store
        self.catalog = catalog
        self.usage = usage or (lambda _n, _v: [])
        self.cache_dir = config.cache_dir.resolve()
        (self.cache_dir / "artifacts").mkdir(parents=True, exist_ok=True, mode=0o700)
        self._fetch_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------ validation

    async def validate(self, data: bytes) -> ValidatedPackage:
        try:
            return await asyncio.to_thread(inspect_archive, data, self.policy)
        except PackageValidationError as exc:
            raise PackageInvalidError("package validation failed", details=exc.errors) from None

    # ------------------------------------------------------------ registration

    async def register(self, data: bytes, *, actor: str) -> tuple[PackageRecord, bool]:
        """Store a new immutable version. Returns (record, created)."""
        package = await self.validate(data)
        meta = package.metadata
        existing = await self.catalog.get(meta.name, meta.version)
        if existing is not None:
            return self._same_or_conflict(existing, package), False

        key = artifact_key(meta.name, meta.version, package.digest)
        stored_now = True
        try:
            uri = await self.store.put(key, package.artifact)
        except ArtifactExistsError:
            # Same key means same digest, i.e. identical bytes already stored.
            stored_now = False
            uri = self.store.uri(key)
        record = PackageRecord(
            name=meta.name,
            version=meta.version,
            digest=package.digest,
            size=package.size,
            artifact_uri=uri,
            files=package.files,
            metadata=meta,
            lock_digest=package.lock_digest,
            created_at=datetime.now(UTC),
            created_by=actor,
        )
        try:
            await self.catalog.insert(record)
        except VersionExistsError:
            winner = await self.catalog.get(meta.name, meta.version)
            if winner is None:  # deleted in between; extremely unlikely
                raise PackageConflictError("concurrent modification, retry") from None
            if winner.digest != record.digest and stored_now:
                with contextlib.suppress(Exception):
                    await self.store.delete(key)
            return self._same_or_conflict(winner, package), False
        audit.info(
            "package registered",
            extra={
                "principal": actor,
                "package": meta.name,
                "version": meta.version,
                "digest": package.digest,
            },
        )
        return record, True

    @staticmethod
    def _same_or_conflict(existing: PackageRecord, package: ValidatedPackage) -> PackageRecord:
        if existing.digest == package.digest:
            return existing
        raise PackageConflictError(
            f"{existing.key} already exists with different content; versions are immutable, "
            "publish the change as a new version"
        )

    async def register_directory(self, path: Path, *, actor: str) -> tuple[PackageRecord, bool]:
        if not self.config.allow_directory_registration:
            raise PackageForbiddenError(
                "directory registration is disabled (packages.allow_directory_registration)"
            )
        try:
            resolved = await asyncio.to_thread(path.resolve, True)
        except OSError:
            raise PackageNotFoundError(f"directory not found: {path}") from None
        roots = [r.resolve() for r in self.config.directory_roots]
        if not any(resolved == r or r in resolved.parents for r in roots):
            raise PackageForbiddenError("directory is not under packages.directory_roots")
        try:
            data = await asyncio.to_thread(archive_directory, resolved, self.config.limits)
        except PackageValidationError as exc:
            raise PackageInvalidError("package validation failed", details=exc.errors) from None
        return await self.register(data, actor=actor)

    # ------------------------------------------------------------ queries

    async def get(self, name: str, version: str) -> PackageRecord:
        record = await self.catalog.get(name, version)
        if record is None:
            raise PackageNotFoundError(f"package {name}@{version} not found")
        return record

    async def versions(self, name: str) -> list[PackageRecord]:
        records = await self.catalog.versions(name)
        if not records:
            raise PackageNotFoundError(f"package {name} not found")
        return records

    async def list_packages(self) -> list[dict[str, Any]]:
        out = []
        for name in await self.catalog.names():
            records = await self.catalog.versions(name)
            if records:
                out.append(
                    {"name": name, "versions": [r.version for r in records], "latest": records[-1].version}
                )
        return out

    # ------------------------------------------------------------ integrity

    async def verify(self, name: str, version: str) -> dict[str, Any]:
        """Download the stored artifact and check digest, structure and manifest."""
        record = await self.get(name, version)
        with tempfile.TemporaryDirectory(prefix="omg-verify-") as tmp:
            artifact = Path(tmp) / "artifact.zip"
            try:
                await self.store.download(artifact_key(name, version, record.digest), artifact)
            except ArtifactNotFoundError:
                return {"ok": False, "error": "artifact missing from storage", "digest": record.digest}
            try:
                await asyncio.to_thread(
                    extract_verified, artifact, record.digest, record.files, Path(tmp) / "pkg", self.policy
                )
            except IntegrityError as exc:
                return {"ok": False, "error": str(exc), "digest": record.digest}
            finally:
                make_writable(Path(tmp))
        return {"ok": True, "digest": record.digest, "files": len(record.files), "size": record.size}

    async def fetch_verified(self, record: PackageRecord) -> Path:
        """Return a local copy of the artifact whose digest has just been verified."""
        lock = self._fetch_locks.setdefault(record.digest, asyncio.Lock())
        async with lock:
            path = self.cache_dir / "artifacts" / f"{record.digest}.zip"
            if path.exists():
                if await asyncio.to_thread(sha256_file, path) == record.digest:
                    return path
                logger.warning("cached artifact corrupt; re-downloading", extra={"package": record.key})
                path.unlink()
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".download-")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                try:
                    await self.store.download(artifact_key(record.name, record.version, record.digest), tmp)
                except ArtifactNotFoundError:
                    raise PackageIntegrityError(
                        f"artifact for {record.key} is missing from storage"
                    ) from None
                actual = await asyncio.to_thread(sha256_file, tmp)
                if actual != record.digest:
                    raise PackageIntegrityError(
                        f"artifact checksum mismatch for {record.key}: refusing to run it"
                    )
                os.replace(tmp, path)
            finally:
                await asyncio.to_thread(tmp.unlink, True)
            return path

    # ------------------------------------------------------------ deletion

    async def delete(self, name: str, version: str, *, actor: str) -> None:
        retention = self.config.retention
        if not retention.allow_delete:
            raise PackageForbiddenError(
                "deleting package versions is disabled (packages.retention.allow_delete)"
            )
        records = await self.versions(name)
        record = next((r for r in records if r.version == version), None)
        if record is None:
            raise PackageNotFoundError(f"package {name}@{version} not found")
        if retention.keep_latest and record in records[-retention.keep_latest :]:
            raise PackageForbiddenError(
                f"the newest {retention.keep_latest} version(s) are retained (packages.retention.keep_latest)"
            )
        users = self.usage(name, version)
        if users:
            raise PackageConflictError(f"version is used by server(s): {', '.join(sorted(users))}")
        await self.catalog.delete(name, version)
        try:
            await self.store.delete(artifact_key(name, version, record.digest))
        except Exception:
            logger.warning("artifact deletion failed; it is now orphaned", extra={"package": record.key})
        audit.info("package deleted", extra={"principal": actor, "package": name, "version": version})

    async def close(self) -> None:
        await self.catalog.close()
        await self.store.close()


def make_writable(root: Path) -> None:
    """Undo read-only permissions so a tree can be removed."""
    for dirpath, _dirnames, _filenames in os.walk(root):
        with contextlib.suppress(OSError):
            os.chmod(dirpath, 0o700)


def build_package_service(config: PackagesConfig, usage: UsageLookup | None = None) -> PackageService:
    store: ArtifactStore
    arts = config.artifacts
    if arts.backend == "s3":
        assert arts.bucket is not None
        store = S3ArtifactStore(
            bucket=arts.bucket,
            prefix=arts.prefix,
            endpoint_url=arts.endpoint_url,
            region=arts.region,
            access_key_id=_env(arts.access_key_id_env),
            secret_access_key=_env(arts.secret_access_key_env),
        )
    else:
        store = LocalArtifactStore(arts.path)
    catalog: PackageCatalog
    cat = config.catalog
    if cat.backend == "mongodb":
        uri = os.environ.get(cat.mongodb_uri_env)
        if not uri:
            from openmcp_gateway.config import ConfigError

            raise ConfigError(f"packages.catalog: environment variable {cat.mongodb_uri_env} is not set")
        from openmcp_gateway.redaction import redactor

        redactor.register([uri])  # connection strings usually embed a password
        catalog = MongoPackageCatalog(uri, cat.database, cat.collection)
    else:
        catalog = SqlitePackageCatalog(cat.path)
    return PackageService(config, store, catalog, usage=usage)


def _env(name: str | None) -> str | None:
    if name is None:
        return None
    value = os.environ.get(name)
    if value is None:
        from openmcp_gateway.config import ConfigError

        raise ConfigError(f"packages.artifacts: environment variable {name} is not set")
    from openmcp_gateway.redaction import redactor

    redactor.register([value])
    return value
