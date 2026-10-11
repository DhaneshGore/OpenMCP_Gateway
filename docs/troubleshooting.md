# Troubleshooting

Start with:

```bash
openmcp --config openmcp.yaml config validate
openmcp --config openmcp.yaml doctor          # launches each server once
OPENMCP_LOGGING__LEVEL=debug OPENMCP_LOGGING__FORMAT=text openmcp --config openmcp.yaml serve
```

With an admin token in `OPENMCP_ADMIN_TOKEN`:

```bash
openmcp server list
openmcp server inspect <id>      # state, last error, protocol version, counters
openmcp server logs <id>         # the server's own stderr (redacted)
```

## JSON-RPC error codes returned by the gateway

| Code | Meaning | What to do |
| --- | --- | --- |
| `-32602` | Unknown tool/prompt/resource, **or** not permitted for your token, **or** server disabled | Check the namespaced name (`server__tool`) and your token's `servers` list. The gateway deliberately does not say which. |
| `-32001` | Upstream did not answer within the timeout | Raise `servers[].timeouts.request`, or investigate the server (`server logs`). |
| `-32000` | Upstream connection closed (process exited or remote disconnected) | `server inspect` shows `last_error`; the restart policy applies. |
| `-32010` | Upstream unavailable: failed to start, in restart backoff, restart limit reached, stopped by an operator, or unreachable | `server inspect`; after fixing, `server start <id>` clears backoff and limits. |
| `-32011` | Overloaded: per-server concurrency and queue full, or queue wait exceeded | Raise `max_concurrency` / `max_queue` / `queue_timeout`, or slow the client down. |
| `-32012` | Rate limited; `data.retryAfterSeconds` says when to retry | Adjust `rate_limits`. Note that every MCP request counts, including listings. |
| `-32013` | Upstream sent a result that does not match the MCP schema | Upstream bug; check its version. |
| `-32603` | Internal gateway error | Check gateway logs (search for the request id); please report it. |
| other | Forwarded unchanged from the upstream server | See the upstream's documentation. |

## HTTP status codes on `/mcp`

| Status | Cause |
| --- | --- |
| 401 | Missing or invalid bearer token (`auth.mode: bearer`). |
| 404 | Unknown or expired `Mcp-Session-Id`, or the session belongs to a different token. Reinitialize. |
| 413 | Request body over `limits.max_request_body_bytes`. |
| 421 / 403 | Host or Origin header not allowed. Add your hostname to `gateway.allowed_hosts` / `allowed_origins`. |
| 503 | `limits.max_sessions` reached. |

## Common problems

**`refusing to listen on non-loopback address … without authentication`.**
Set up tokens (`auth.mode: bearer`), or acknowledge the risk with
`auth.allow_unauthenticated_non_loopback: true`.

**`command not found`.** The child's `PATH` is the gateway's `PATH`. Use an
absolute `command`, or make sure the runtime (node, uvx, …) is installed where
the gateway runs, including inside containers.

**A server works in my shell but not in the gateway.** It probably needs an
environment variable your shell has. Servers don't inherit the gateway's
environment; add the variable to `inherit_env`, `env`, or `env_from_host`.

**`required gateway environment variable(s) not set: X`.** An `env_from_host`
or `headers_from_host` entry refers to a variable that is missing from the
gateway's environment.

**`destination not allowed by egress policy`.** The remote server resolves to a
private, loopback or link-local address. If that's intended (an internal
service), add its range to `security.allowed_egress_networks` or set
`allow_private_network: true` on that server.

**`plain http:// requires allow_insecure_http: true`.** Use `https://`, or
opt in for a trusted local endpoint.

**Tools from one server are missing from `tools/list`.** The server failed to
start or list. The listing omits it rather than failing the whole request. See
`server inspect <id>` and the gateway logs (`omitting server from listing`).

**`server … failed repeatedly and will not be restarted automatically`.** The
restart cap (`restart.max_restarts` within `restart.window`) was reached. Fix
the cause, then `openmcp server start <id>`.

**`was stopped by an operator`.** Someone ran `server stop`. Run
`server start <id>`.

**Rate limited right after connecting.** SDK clients often call `tools/list`
before `tools/call`, and both count against `rate_limits`.
