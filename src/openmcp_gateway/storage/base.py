"""Storage interfaces (the storage-adapter extension point).

Only servers registered at runtime through the admin API are persisted here;
servers declared in the configuration file always come from the file.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import TypeAdapter

from openmcp_gateway.config import ServerConfig

SERVER_ADAPTER: TypeAdapter[ServerConfig] = TypeAdapter(ServerConfig)


class ServerRepository(Protocol):
    async def list(self) -> list[ServerConfig]: ...

    async def get(self, server_id: str) -> ServerConfig | None: ...

    async def put(self, config: ServerConfig) -> None:
        """Insert or replace."""
        ...

    async def delete(self, server_id: str) -> bool:
        """Return True if something was deleted."""
        ...

    async def close(self) -> None: ...
