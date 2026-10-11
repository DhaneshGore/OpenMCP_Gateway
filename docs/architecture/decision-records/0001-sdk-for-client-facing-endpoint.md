# 0001. Use the official MCP SDK for the client-facing endpoint

* Status: Accepted
* Date: 2026-10-11

## Context

The client-facing side must implement Streamable HTTP correctly across five
protocol revisions in two eras (handshake-based 2024-11-05 … 2025-11-25 and the
stateless 2026-07-28 envelope), including session ids, SSE vs JSON responses,
Origin/Host validation, body-size limits, cancellation and version
negotiation. The official `mcp` SDK (2.3.0 at time of writing) ships all of
this in `StreamableHTTPSessionManager` + `mcp.server.lowlevel.Server`.

## Options considered

1. Implement Streamable HTTP ourselves on FastAPI.
2. Use the SDK's high-level `MCPServer` (decorator-based tools).
3. Use the SDK's **lowlevel** `Server` with handler callables, mounted as an
   ASGI route in our FastAPI app.

## Decision

Option 3. The lowlevel server lets the gateway answer `tools/list` and
`tools/call` dynamically (tools are discovered at runtime, not declared in
code), while the SDK owns transport correctness. The SDK is imported only in
`downstream/` (and `upstream/http.py` when added) so an SDK major upgrade is
contained.

## Consequences

* We inherit SDK behaviour such as session-to-credential binding and the
  default `max_sessions` / idle timeout; the gateway exposes these as config.
* The SDK's dependency set (`httpx2`, `sse-starlette`, `opentelemetry-api`,
  `pyjwt`, …) becomes ours. We pin `mcp>=2.3,<3`.
* Some SDK APIs are marked provisional (middleware). We avoid them.
