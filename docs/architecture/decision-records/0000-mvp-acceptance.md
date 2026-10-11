# 0000. MVP acceptance criteria

* Status: Accepted
* Date: 2026-10-11

## Context

We need an objective bar for calling the first release an MVP.

## Decision

The MVP is complete when **all** of the following are true and verified by
automated tests or a documented manual check:

1. `pip install .` succeeds in a clean virtual environment on Python 3.11 and
   the newest supported Python.
2. `openmcp serve --config <file>` starts the gateway; `/health`, `/ready` and
   `/version` respond.
3. A real stdio MCP server subprocess (the bundled example) is launched by the
   gateway.
4. An MCP client using the official SDK can initialize against `/mcp`, list the
   upstream's tools (namespaced) and call one, over both the handshake era and
   the 2026-07-28 era.
5. 50 concurrent tool calls with deliberately randomized upstream latency all
   return their own correct result.
6. Upstream timeouts, crashes, malformed output and unknown response ids
   produce defined errors and do not hang or leak pending requests.
7. With `auth.mode: bearer`, requests without a valid token receive 401 and a
   principal cannot call tools on servers outside its allowlist.
8. The gateway refuses to start unauthenticated on a non-loopback address
   unless explicitly overridden.
9. `ruff`, `mypy` and `pytest` pass in CI without private credentials.
10. Every command in README/getting-started has been executed against the
    code.

## Consequences

Remote upstreams, persistence and OAuth are explicitly **not** MVP criteria.
