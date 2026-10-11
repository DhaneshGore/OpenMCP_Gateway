# 0003. Tool namespacing with `server__tool`

* Status: Accepted
* Date: 2026-10-11

## Context

Several upstreams may expose tools with the same name (`search`, `read_file`).
A single aggregated endpoint must expose collision-free names that map back to
exactly one (server, tool) pair, and that are valid MCP tool names
(`[A-Za-z0-9_.-]`, 1–128 chars per the 2025-11-25 guidance).

## Options considered

1. Prefix with a separator: `server__tool`.
2. Keep original names, reject collisions.
3. Per-server endpoints only.

## Decision

Option 1. Server ids are restricted to `^[a-z0-9][a-z0-9-]{0,47}$` — they can
never contain `_` — so splitting at the **first** `__` is unambiguous even when
the upstream tool name itself contains `__`. Every exposed tool also carries
`_meta["openmcp-gateway/upstream"] = {"server": ..., "name": ...}` so the
original identity is never hidden.

## Consequences

* Tool names change from the upstream's perspective; documented prominently.
* A combined name over 128 characters is still exposed but logged as a warning.
* Per-server endpoints (option 3) remain a possible future addition.
