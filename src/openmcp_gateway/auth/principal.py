from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """An authenticated caller. `servers` contains server ids or '*'."""

    name: str
    servers: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)
    anonymous: bool = False

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def can_use(self, server_id: str) -> bool:
        return "*" in self.servers or server_id in self.servers


ANONYMOUS = Principal(name="anonymous", servers=frozenset({"*"}), anonymous=True)
