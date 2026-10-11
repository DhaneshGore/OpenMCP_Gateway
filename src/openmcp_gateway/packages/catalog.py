"""Package catalog: metadata, versions, checksums and artifact URIs.

The catalog is the source of truth for which versions exist. (name, version)
is unique and records are never updated, only inserted or deleted.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol

from openmcp_gateway.packages.metadata import PackageRecord


class VersionExistsError(Exception):
    pass


class PackageCatalog(Protocol):
    async def insert(self, record: PackageRecord) -> None:
        """Insert a new version. Raises VersionExistsError if (name, version) exists."""
        ...

    async def get(self, name: str, version: str) -> PackageRecord | None: ...

    async def versions(self, name: str) -> list[PackageRecord]:
        """All versions of a package, oldest first."""
        ...

    async def names(self) -> list[str]: ...

    async def delete(self, name: str, version: str) -> bool: ...

    async def close(self) -> None: ...


class SqlitePackageCatalog:
    """Default catalog. One file, process-local locking: one gateway per file."""

    MIGRATIONS = (
        """
        CREATE TABLE package_versions (
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            record_json TEXT NOT NULL,
            PRIMARY KEY (name, version)
        )
        """,
    )

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS package_schema (version INTEGER PRIMARY KEY)")
            current = int(
                self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM package_schema").fetchone()[0]
            )
            if current > len(self.MIGRATIONS):
                raise RuntimeError(f"package catalog schema v{current} is newer than this gateway supports")
            for version, script in enumerate(self.MIGRATIONS[current:], start=current + 1):
                self._conn.execute("BEGIN")
                try:
                    self._conn.execute(script)
                    self._conn.execute("INSERT INTO package_schema (version) VALUES (?)", (version,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    async def insert(self, record: PackageRecord) -> None:
        def run() -> None:
            try:
                self._run(
                    "INSERT INTO package_versions (name, version, digest, created_at, record_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        record.name,
                        record.version,
                        record.digest,
                        record.created_at.isoformat(),
                        record.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise VersionExistsError(record.key) from None

        await asyncio.to_thread(run)

    async def get(self, name: str, version: str) -> PackageRecord | None:
        rows = await asyncio.to_thread(
            self._run,
            "SELECT record_json FROM package_versions WHERE name = ? AND version = ?",
            (name, version),
        )
        return PackageRecord.model_validate_json(rows[0][0]) if rows else None

    async def versions(self, name: str) -> list[PackageRecord]:
        rows = await asyncio.to_thread(
            self._run,
            "SELECT record_json FROM package_versions WHERE name = ? ORDER BY created_at, version",
            (name,),
        )
        return [PackageRecord.model_validate_json(r[0]) for r in rows]

    async def names(self) -> list[str]:
        rows = await asyncio.to_thread(self._run, "SELECT DISTINCT name FROM package_versions ORDER BY name")
        return [str(r[0]) for r in rows]

    async def delete(self, name: str, version: str) -> bool:
        def run() -> bool:
            with self._lock:
                cur = self._conn.execute(
                    "DELETE FROM package_versions WHERE name = ? AND version = ?", (name, version)
                )
                return cur.rowcount > 0

        return await asyncio.to_thread(run)

    async def close(self) -> None:
        with self._lock:
            self._conn.close()


class MongoPackageCatalog:
    """MongoDB catalog (install the `mongodb` extra). A unique index enforces immutability."""

    def __init__(self, uri: str, database: str, collection: str) -> None:
        try:
            from pymongo import AsyncMongoClient
        except ImportError:  # pragma: no cover
            raise RuntimeError(
                "MongoDB requires the 'mongodb' extra: pip install 'openmcp-gateway[mongodb]'"
            ) from None
        self._client: Any = AsyncMongoClient(uri, serverSelectionTimeoutMS=10_000, tz_aware=True)
        self._coll: Any = self._client[database][collection]
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def _ensure_indexes(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if not self._ready:
                await self._coll.create_index([("name", 1), ("version", 1)], unique=True, name="name_version")
                await self._coll.create_index([("name", 1), ("created_at", 1)], name="name_created")
                self._ready = True

    @staticmethod
    def _to_doc(record: PackageRecord) -> dict[str, Any]:
        doc: dict[str, Any] = json.loads(record.model_dump_json())
        doc["created_at"] = record.created_at  # native date for sorting/TTL tooling
        doc["_id"] = record.key
        return doc

    @staticmethod
    def _from_doc(doc: dict[str, Any]) -> PackageRecord:
        doc = {k: v for k, v in doc.items() if k != "_id"}
        return PackageRecord.model_validate(doc)

    async def insert(self, record: PackageRecord) -> None:
        from pymongo.errors import DuplicateKeyError

        await self._ensure_indexes()
        try:
            await self._coll.insert_one(self._to_doc(record))
        except DuplicateKeyError:
            raise VersionExistsError(record.key) from None

    async def get(self, name: str, version: str) -> PackageRecord | None:
        await self._ensure_indexes()
        doc = await self._coll.find_one({"name": name, "version": version})
        return self._from_doc(doc) if doc else None

    async def versions(self, name: str) -> list[PackageRecord]:
        await self._ensure_indexes()
        cursor = self._coll.find({"name": name}).sort([("created_at", 1), ("version", 1)])
        return [self._from_doc(doc) async for doc in cursor]

    async def names(self) -> list[str]:
        await self._ensure_indexes()
        return sorted(await self._coll.distinct("name"))

    async def delete(self, name: str, version: str) -> bool:
        await self._ensure_indexes()
        result = await self._coll.delete_one({"name": name, "version": version})
        return bool(result.deleted_count)

    async def close(self) -> None:
        await self._client.close()
