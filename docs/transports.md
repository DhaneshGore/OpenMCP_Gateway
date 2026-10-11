# Transports and protocol support

## Summary

| Side | Transport | Protocol revisions | Status |
| --- | --- | --- | --- |
| Client → gateway | Streamable HTTP at `gateway.mcp_path` (default `/mcp`) | 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25 (initialize handshake); 2026-07-28 | implemented (official SDK) |
| Gateway → local server | stdio subprocess | gateway offers 2025-11-25 and accepts any handshake revision the server answers with | implemented |
| Gateway → remote server | Streamable HTTP | `protocol: auto` probes 2026-07-28 and falls back to the handshake; `legacy` forces the handshake | implemented (official SDK client) |
| Gateway → remote server | legacy HTTP+SSE (2024-11-05 transport) | n/a | not implemented |
| Client → gateway | stdio | n/a | not implemented |
| Gateway → package server | stdio, launched from a verified package version in a sandbox | as for stdio upstreams | implemented ([packages.md](packages.md)) |

`GET /version` reports the revisions the running build supports.

## Client-facing endpoint

The gateway serves one MCP endpoint: `http://<host>:<port><mcp_path>`. Session
handling, SSE vs JSON responses, `Mcp-Session-Id`, protocol-version
negotiation and cancellation are handled by the official SDK's Streamable HTTP
implementation (`StreamableHTTPSessionManager`). The gateway adds:

* authentication before the request reaches the SDK (401 otherwise);
* each session bound to the credential that created it (requests with another
  credential get 404, as if the session did not exist);
* Host/Origin validation (DNS-rebinding protection), automatic for loopback
  binds and configured with `gateway.allowed_hosts` / `allowed_origins`
  otherwise;
* request-body limit (`limits.max_request_body_bytes`, HTTP 413);
* maximum concurrent sessions and idle expiry (`limits.max_sessions`,
  `limits.session_idle_timeout`).

The gateway advertises the `tools`, `prompts` and `resources` capabilities.

### Operations

| Method | Behaviour |
| --- | --- |
| `tools/list` | Tools of every enabled server the caller may use, namespaced. Servers that fail to start are omitted (and logged); the listing still succeeds. |
| `tools/call` | Routed by namespace. Unknown, disabled or forbidden → `-32602`. |
| `prompts/list`, `prompts/get` | Same namespacing as tools. |
| `resources/list` | Upstream URIs are kept unchanged (they may appear inside tool results). A URI offered by two visible servers is omitted as ambiguous. |
| `resources/read` | Routed to the single visible server that listed the URI. |
| `notifications/progress` | Relayed from upstream to the requesting client. The gateway issues its own progress token upstream and only accepts progress for tokens it issued to that server. |
| `notifications/cancelled` | A client cancellation cancels the gateway handler, which sends `notifications/cancelled` upstream. |
| `ping` | Answered by the gateway. |

Not forwarded yet: `resources/templates/list`, `resources/subscribe`,
`completion/complete`, `logging/setLevel`, and `*/list_changed`
notifications to clients. Server-initiated requests from upstreams (sampling,
elicitation, roots) are answered with `-32601`; the gateway advertises no
client capabilities upstream, so well-behaved servers will not send them.

Upstream results are validated against the SDK's protocol models before being
returned. Fields that those models don't define are dropped, and an invalid
result becomes error `-32013`.

## Namespacing

Exposed name = `<server id>__<upstream name>` (two underscores). Server ids
match `^[a-z0-9][a-z0-9-]{0,47}$` and can't contain `_`, so the first `__`
is always the boundary. `github__create_issue` routes to tool `create_issue`
on server `github`, and `x__a__b` routes to tool `a__b` on server `x`.

Each exposed tool and prompt carries its origin in `_meta`:

```json
{"name": "github__create_issue",
 "_meta": {"openmcp-gateway/upstream": {"server": "github", "name": "create_issue"}}}
```

Names over 128 characters are still exposed but logged as a warning (some
clients reject them). See ADR 0003.

## stdio upstreams

* Launch: `exec` of `command` + `args` (no shell), own process group,
  minimal environment (see [security.md](security.md#subprocess-environment)).
* Framing: newline-delimited JSON-RPC on stdin/stdout. Lines longer than
  `limits.max_upstream_message_bytes` are discarded (the request they belonged
  to times out) without disturbing other requests.
* Multiplexing: every request gets a fresh integer id. Responses are matched
  by id only, so concurrent and out-of-order responses are fine; unknown,
  duplicate and late responses are counted and dropped.
* stderr: drained continuously into a bounded, redacted buffer, viewable with
  `openmcp server logs <id>`.
* Lifecycle: `initialize` → `notifications/initialized` within
  `timeouts.startup`; on exit or crash, pending requests fail with `-32000`
  and the restart policy applies.
* Shutdown: close stdin, wait `timeouts.shutdown`, SIGTERM the process group,
  wait, SIGKILL.

## Remote (Streamable HTTP) upstreams

* Built on the SDK client. One logical connection (session) per server, held
  open for the server's lifetime.
* The gateway authenticates with **its own** credentials (`headers`,
  `headers_from_host`); client tokens are never passed through.
* Every outbound connection goes through the egress guard; see
  [security.md](security.md#outbound-connections-ssrf).
* `https://` is required unless `allow_insecure_http: true`.
* HTTP proxies from the environment (`HTTP_PROXY`, …) are ignored, because a
  proxy would bypass address validation.
* A connection failure marks the server failed; it reconnects under the same
  restart policy as stdio servers.
