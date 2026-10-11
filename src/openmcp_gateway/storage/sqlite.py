"""SQLite registry with versioned migrations.

SQLite access is blocking, so every call runs in a worker thread through a
single connection guarded by a lock. The schema is deliberately plain (one row
per server with its validated JSON definition) so a PostgreSQL adapter can
mirror it. A process-local lock is *not* cross-process coordination: run one
gateway per database file.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from openmcp_gateway.config import ServerConfig
from openmcp_gateway.storage.base import SERVER_ADAPTER

# Append-only. Never edit an applied migration; add a new one.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE servers (
        id TEXT PRIMARY KEY,
        transport TEXT NOT NULL,
        config_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
)


class SchemaTooNewError(RuntimeError):
    pass


class SqliteServerRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @property
    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0])

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = int(
                self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            )
            if current > len(MIGRATIONS):
                raise SchemaTooNewError(
                    f"database {self._path} has schema v{current}; "
                    f"this version understands up to v{len(MIGRATIONS)}"
                )
            for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
                self._conn.execute("BEGIN")
                try:
                    for statement in filter(str.strip, script.split(";")):
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, _now())
                    )
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    def _run(self, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    async def list(self) -> list[ServerConfig]:
        rows = await asyncio.to_thread(self._run, "SELECT config_json FROM servers ORDER BY id")
        return [SERVER_ADAPTER.validate_python(json.loads(str(r[0]))) for r in rows]

    async def get(self, server_id: str) -> ServerConfig | None:
        rows = await asyncio.to_thread(
            self._run, "SELECT config_json FROM servers WHERE id = ?", (server_id,)
        )
        return SERVER_ADAPTER.validate_python(json.loads(str(rows[0][0]))) if rows else None

    async def put(self, config: ServerConfig) -> None:
        payload = json.dumps(SERVER_ADAPTER.dump_python(config, mode="json"))
        now = _now()
        await asyncio.to_thread(
            self._run,
            "INSERT INTO servers (id, transport, config_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET transport=excluded.transport, config_json=excluded.config_json, "
            "updated_at=excluded.updated_at",
            (config.id, config.transport, payload, now, now),
        )

    async def delete(self, server_id: str) -> bool:
        def run() -> bool:
            with self._lock:
                return self._conn.execute("DELETE FROM servers WHERE id = ?", (server_id,)).rowcount > 0

        return await asyncio.to_thread(run)

    async def close(self) -> None:
        with self._lock:
            self._conn.close()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
