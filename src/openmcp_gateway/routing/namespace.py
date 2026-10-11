"""Namespacing of upstream names into the gateway's flat name space (ADR 0003).

Exposed name = ``<server_id>__<upstream_name>``. Server ids cannot contain
``_``, so the first ``__`` is always the boundary, even when the upstream name
itself contains ``__``.
"""

from __future__ import annotations

from openmcp_gateway.config import SERVER_ID_RE

SEPARATOR = "__"
MAX_RECOMMENDED_TOOL_NAME = 128
META_KEY = "openmcp-gateway/upstream"


def qualify(server_id: str, name: str) -> str:
    return f"{server_id}{SEPARATOR}{name}"


def split(qualified: str) -> tuple[str, str] | None:
    """Return (server_id, upstream_name), or None if `qualified` is not a gateway name."""
    idx = qualified.find(SEPARATOR)
    if idx <= 0:
        return None
    server_id, name = qualified[:idx], qualified[idx + len(SEPARATOR) :]
    if not name or not SERVER_ID_RE.match(server_id):
        return None
    return server_id, name


def origin_meta(server_id: str, name: str) -> dict[str, dict[str, str]]:
    return {META_KEY: {"server": server_id, "name": name}}
