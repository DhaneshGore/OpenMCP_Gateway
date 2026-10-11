from __future__ import annotations

from openmcp_gateway.config import ServerConfig


class MemoryServerRepository:
    """Process-local, non-persistent registry (the default)."""

    def __init__(self) -> None:
        self._items: dict[str, ServerConfig] = {}

    async def list(self) -> list[ServerConfig]:
        return list(self._items.values())

    async def get(self, server_id: str) -> ServerConfig | None:
        return self._items.get(server_id)

    async def put(self, config: ServerConfig) -> None:
        self._items[config.id] = config

    async def delete(self, server_id: str) -> bool:
        return self._items.pop(server_id, None) is not None

    async def close(self) -> None:
        return None
