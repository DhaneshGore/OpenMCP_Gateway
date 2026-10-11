# 0002. Own JSON-RPC multiplexer for stdio upstreams

* Status: Accepted
* Date: 2026-10-11

## Context

The SDK's `Client`/`stdio_client` can talk to a stdio server, and correlates
responses by id. The gateway, however, needs control the SDK client does not
expose as configuration: a hard bound on pending requests, a per-message size
cap with resynchronisation, bounded stderr capture with redaction, explicit
counters for late/unknown/malformed messages, process-group termination, and a
lifecycle the gateway's restart policy can drive.

## Options considered

1. SDK `Client` over `stdio_client`, wrapped.
2. A small purpose-built multiplexer (`upstream/jsonrpc.py`) plus subprocess
   transport (`upstream/stdio.py`), using `mcp_types` constants and models for
   protocol data.

## Decision

Option 2 for stdio. The multiplexer is ~300 lines, transport-agnostic, and
exhaustively tested against out-of-order, duplicate, late, unknown and
malformed responses. For **remote HTTP upstreams** we will use the SDK client
(option 1), because Streamable HTTP session/SSE handling is much larger and the
SDK accepts a custom HTTP client through which SSRF controls can be applied.

## Consequences

* We own correctness of the stdio JSON-RPC framing — mitigated by tests.
* Upstream stdio servers are driven over the handshake era (offering
  2025-11-25). Adding the 2026-07-28 era for stdio upstreams is planned work.
