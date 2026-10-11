# Architecture

> Status legend used throughout this document: **[implemented]** — in the code
> and covered by tests; **[planned]** — designed, not yet built. See
> [ROADMAP.md](ROADMAP.md) for sequencing.

OpenMCP Gateway (working name) is a single-process service that sits between
MCP **clients** (agents, IDEs, chat apps) and one or more upstream MCP
**servers** (local subprocesses or remote HTTP endpoints). Clients see one MCP
server; the gateway routes each request to the right upstream, enforces
authentication, authorization and resource limits, and gives operators a
health/admin/metrics surface.

```text
          MCP clients                                  upstream MCP servers
 ┌───────────────────────┐                         ┌──────────────────────────┐
 │ Streamable HTTP client │──POST/GET /mcp──┐      │ stdio subprocess (local) │
 └───────────────────────┘                  │      └────────────▲─────────────┘
                                            ▼                   │ newline-delimited
 ┌──────────────────────────────────────────────────────┐       │ JSON-RPC
 │  FastAPI / Starlette app                              │       │
 │  ┌────────────┐  ┌───────────────────────────────┐   │  ┌────┴────────────────┐
 │  │ auth       │→ │ MCP SDK StreamableHTTP session │   │  │ StdioUpstream       │
 │  │ middleware │  │ manager + lowlevel Server      │   │  │  - reader task      │
 │  └────────────┘  └──────────────┬────────────────┘   │  │  - pending{id→fut}  │
 │  /health /ready /version        │ handlers           │  │  - stderr ring buf  │
 │  /admin/* (token + admin role)  ▼                    │  └────▲────────────────┘
 │                   ┌─────────────────────────┐        │       │
 │                   │ GatewayService           │        │  ┌────┴────────────────┐
 │                   │  router · authz · limits │───────────▶│ ServerManager        │
 │                   └─────────────────────────┘        │  │  lifecycle, restart │
 │                                                       │  │  backoff, semaphores│
 └───────────────────────────────────────────────────────┘  └────┬────────────────┘
                                                                   │
                                                            ┌──────▼──────────────┐
                                                            │ HttpUpstream (SDK    │──► remote MCP server
                                                            │ client, SSRF-guarded)│    (Streamable HTTP)
                                                            └─────────────────────┘
```

## 1. Protocol scope

The gateway is built on the official MCP Python SDK (`mcp` 2.x, wire types in
`mcp-types`). Facts verified against SDK 2.3.0:

| Revision | Era | Client-facing (`/mcp`) | Upstream stdio |
| --- | --- | --- | --- |
| 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25 | handshake (`initialize`) | served by SDK **[implemented]** | gateway offers 2025-11-25, accepts any handshake revision the server answers with **[implemented]** |
| 2026-07-28 | modern (stateless per-request envelope, `server/discover`) | served by SDK **[implemented]** | **[planned]**; stdio upstreams are driven over the handshake era today. Remote HTTP upstreams use it when available (`protocol: auto`) **[implemented]** |

The client-facing era is chosen by the client; the gateway translates to the
upstream's negotiated era. Because the gateway re-originates every upstream
request (it is an MCP *client* toward upstreams and an MCP *server* toward
clients), the two sides are independent JSON-RPC conversations.

Operations forwarded **[implemented]**: `tools/list`, `tools/call`,
`prompts/list`, `prompts/get`, `resources/list`, `resources/read`, and
`notifications/progress` (upstream → client). **[planned]**:
`resources/templates/list`, subscriptions, completions, `list_changed`
propagation. Server→client requests
(sampling, elicitation, roots) arriving from an upstream are answered with
`-32601 Method not found` — the gateway does not proxy them yet, and says so in
the capabilities it advertises upstream (it advertises none).

## 2. Module boundaries

```text
src/openmcp_gateway/
  config.py            Pydantic models + YAML loader + env overrides
  app.py               FastAPI factory, lifespan, route wiring
  cli.py               `openmcp` command (argparse, no extra deps)
  logconfig.py         JSON log formatter + redaction filter + request ids
  redaction.py         secret redaction (registered values + patterns)
  errors.py            gateway error taxonomy → JSON-RPC codes
  gateway.py           GatewayService: authz, routing, aggregation, rate limits
  api/                 health/ready/version, admin API, ASGI middleware
  downstream/          MCP server facing clients (SDK lowlevel Server handlers)
  routing/             namespacing (pure functions)
  runtime/             ServerManager: lifecycle, restart policy, concurrency
  upstream/
    base.py              UpstreamConnection protocol (transport extension point)
    jsonrpc.py           transport-agnostic request multiplexer (id → future)
    stdio.py             subprocess transport
    http.py              remote Streamable HTTP transport (SDK client)
  auth/                principals, Authenticator protocol, static bearer tokens
  security/            SSRF guard, subprocess env allowlist, rate limiter
  storage/             ServerRepository protocol, memory + SQLite adapters
  packages/            multi-file server packages (ADR 0006)
    archive.py           validation, canonical artifacts, safe extraction
    artifacts.py         ArtifactStore: local directory, S3-compatible
    catalog.py           PackageCatalog: SQLite, MongoDB
    service.py           register / verify / fetch / delete, retention
    environments.py      cached, hash-locked Python environments
    bootstrap.py         stdlib-only Landlock launcher (runs in the package's interpreter)
    runtime.py           PackageUpstream: verified extraction + sandboxed launch
  observability/       Prometheus metrics
  demo/                demo stdio server used by docs and tests
```

Dependency direction: `api`/`downstream` → `gateway` → `routing`, `auth`,
`runtime` → `upstream` → `jsonrpc`/`security`. Only `downstream/`, `app.py`,
`auth/backends.py` and `upstream/http.py` import the MCP SDK's server, auth
or client modules; everything else uses `mcp_types` for protocol data.

### Internal interfaces (the extension points)

| Interface | Purpose | Implementations |
| --- | --- | --- |
| `UpstreamConnection` | start/stop/request one upstream | `StdioUpstream`, `HttpUpstream` **[implemented]** |
| `Authenticator` | HTTP connection → `Principal` or reject | `NoAuthAuthenticator`, `StaticTokenAuthenticator` **[implemented]**, OAuth 2.1 resource server **[planned]** |
| `ServerRepository` | persist servers registered at runtime | `MemoryServerRepository`, `SqliteServerRepository` **[implemented]**, PostgreSQL **[planned]** |
| `ArtifactStore` | immutable package artifacts | `LocalArtifactStore`, `S3ArtifactStore` **[implemented]** |
| `PackageCatalog` | package versions, digests, manifests | `SqlitePackageCatalog`, `MongoPackageCatalog` **[implemented]** |
| routing (`qualify`/`split`) | name ↔ (server, tool) | `server__tool` namespacing **[implemented]** |

Extension code is ordinary Python imported by configuration that the operator
writes; nothing is discovered or executed automatically.

## 3. Request flow: `tools/call`

1. HTTP request reaches `/mcp`. The authentication middleware resolves a
   `Principal` (or answers 401). The SDK session manager validates Host/Origin
   (DNS-rebinding protection), body size, session id, and binds the session to
   the principal that created it (requests from another credential get 404).
2. The SDK dispatches `tools/call` to `GatewayService.call_tool`.
3. The router splits `github__create_issue` into (`github`, `create_issue`).
   Unknown server, disabled server, or a server the principal may not use all
   produce the **same** error (`-32602 Unknown tool`) so that authorization
   does not leak which servers exist.
4. Per-principal and global rate limits (`-32012`), then the per-server
   concurrency semaphore with a bounded, time-limited wait queue
   (`-32011 overloaded`).
5. `ServerManager.ensure_ready(server)` — single-flight start: concurrent
   callers share one start attempt, so a burst never launches duplicate
   processes.
6. `StdioUpstream.request("tools/call", {...})` allocates a fresh internal
   integer id, registers a future, writes one line to stdin and awaits the
   future with a timeout.
7. The upstream's dedicated reader task parses each stdout line and resolves
   **only** the future registered under that exact id. Unknown/duplicate/late
   ids are counted and dropped; malformed lines are counted and dropped.
8. The result is validated as `CallToolResult` and returned; the SDK writes it
   to the client under the client's original JSON-RPC id.

Client ids never travel upstream and upstream ids never travel downstream, so
one client cannot receive another client's response, regardless of ordering.

## 4. Stdio multiplexer — correctness rules **[implemented]**

* Correlate by id, never by order. Ids are per-connection monotonically
  increasing integers, so an id is never reused within a process lifetime.
* `max_pending` bounds in-flight requests; beyond it, requests fail fast with
  an overload error instead of queueing unboundedly.
* Timeout → the pending entry is removed, `notifications/cancelled` is sent
  upstream (best effort), and the caller gets `REQUEST_TIMEOUT`. A response
  arriving later finds no entry and is dropped (counted as `late`).
* Caller cancellation (client sent `notifications/cancelled`, or disconnected)
  → same cleanup as timeout.
* EOF, crash, or a write failure → every pending future fails with
  `CONNECTION_CLOSED`; the connection transitions to `failed`; the manager
  decides about restarts.
* Lines longer than `max_message_bytes` are discarded up to the next newline
  (the request they belonged to times out); the reader never buffers more than
  the limit.
* Upstream notifications are dispatched to a handler and never treated as
  responses. Upstream requests get an error response (or `{}` for `ping`).
* stderr is drained continuously into a bounded ring buffer (line count and
  line length capped), so a chatty server can never block on a full pipe or
  exhaust gateway memory.

## 5. Server lifecycle **[implemented]**

States: `stopped → starting → ready → (stopping → stopped) | failed`, plus
`disabled`. Failures (start errors, crashes, lost remote connections) are
recorded in a sliding window (`restart.window`). The retry delay is
`restart.backoff_initial × 2^(failures-1)`, capped at `restart.backoff_max`.
Autostart servers are restarted in the background after the delay; others
restart on the next request after it. More than `restart.max_restarts`
failures in the window, or `restart.policy: never`, leaves the server
`failed` until an operator starts it. An operator `stop` holds the server
stopped: client requests do not restart it. Shutdown: close stdin, wait
`timeouts.shutdown`, SIGTERM the process group, wait, SIGKILL.

## 5a. Packages **[implemented]**

A package version is an immutable, content-addressed artifact (canonical ZIP,
SHA-256) recorded in a catalog with its full file manifest. Starting a
`transport: package` server:

```text
catalog.get(name, version) ─► artifact store ─► cache (digest verified)
   ─► environment (cached by lock digest; hash-pinned wheels, sandboxed pip)
   ─► extract into <runtime>/<id>/<random>/pkg (re-validated, per-file sha256, read-only)
   ─► <env>/bin/python -I -B bootstrap.py <spec>
          rlimits ─► Landlock (ro: system, env, pkg; rw: data, tmp; optional no-TCP)
          ─► no_new_privs ─► sys.path[0] = pkg ─► runpy(entrypoint)
   ─► StdioUpstream (same multiplexer, lifecycle and restart policy as stdio servers)
```

Details, limits and the isolation boundary: [docs/packages.md](docs/packages.md), ADR 0006.

## 6. Security model

### Trust boundaries

| Party | Trust |
| --- | --- |
| Operator (writes config file, holds admin token) | trusted |
| MCP client (holds a client token) | authenticated, authorized per server; untrusted input |
| Upstream MCP server process | runs with operator-chosen command; its **output** is untrusted (size-limited, schema-validated, never interpreted as a command) |
| Remote upstream (HTTP) | untrusted network peer; reached only through the connect-time egress guard |

The gateway does **not** sandbox stdio servers: they run as the gateway's OS
user with a minimal environment. Isolation beyond that (containers, seccomp,
separate users) is a deployment concern documented in `docs/security.md`.

### Secure defaults

* Binds `127.0.0.1` by default.
* `auth.mode: none` is refused on a non-loopback bind unless
  `auth.allow_unauthenticated_non_loopback: true` is set explicitly (a
  warning is printed at startup).
* No default credentials. Tokens are stored as SHA-256 hashes; plaintext tokens
  are printed once by `openmcp token generate`.
* The admin API always requires a token with the `admin` role (every request
  is rejected when none is configured; `admin.enabled: false` removes the
  routes). Registering **stdio** servers through the
  API is off by default because it is equivalent to remote code execution.
* Subprocesses: `exec` with an argument vector (never a shell); environment
  built from an allowlist plus explicit per-server values; secrets pulled from
  the gateway's environment by reference and redacted from logs.
* Errors returned to clients are constructed by the gateway; raw exception text
  and upstream stderr are never sent to clients.
* DNS-rebinding protection (Host/Origin allowlist) on `/mcp`, auto-configured
  for loopback binds.

### Threat model (STRIDE summary)

| Threat | Mitigation | Status |
| --- | --- | --- |
| Spoofed client identity | 256-bit bearer tokens looked up by SHA-256 digest; forwarded identity headers ignored | implemented |
| Session hijack across principals | SDK binds sessions to creating credential; unpredictable ids (uuid4) | implemented (SDK) |
| Tool access beyond grant | per-principal server allowlist checked before routing; uniform "unknown tool" error | implemented |
| Command injection via config/API | argv exec, no shell; API stdio registration disabled by default | implemented |
| Secret leakage via logs/errors | redaction filter, sanitized errors, env allowlist | implemented |
| DoS: request floods | body-size limit (SDK), per-server concurrency + queue caps, in-process rate limits | implemented |
| DoS: upstream output floods | message-size caps (stdio line, SSE event, JSON body), bounded stderr buffer | implemented |
| Malicious package archives (traversal, symlinks, bombs, duplicates) | strict validation, canonical re-pack, safe extraction (O_EXCL, O_NOFOLLOW, containment checks) | implemented |
| Tampered or corrupt artifacts | digest verification before every start; per-file manifest check | implemented |
| Package reading another server's files or gateway state | Landlock per process: own `pkg/` read-only, own `data/`/`tmp/` read-write, nothing else | implemented (Linux) |
| Supply-chain via package dependencies | hash-pinned lock, wheels only, operator index, sandboxed install, timeout | implemented |
| SSRF via remote upstream URLs | scheme check, no URL credentials, connect-time resolution + address validation, dial the validated IP, env proxies ignored | implemented |
| Browser-driven attacks on localhost | Host/Origin validation, no CORS, admin token required | implemented |

## 7. Scaling model

One gateway process owns its upstream subprocesses, sessions, concurrency
limits and rate-limit buckets **in memory**. Running several replicas
behind a load balancer requires sticky routing by `Mcp-Session-Id` for
handshake-era clients, and each replica enforces its limits independently
(effective global limit = N × configured limit). Distributed rate limiting and
shared session state are **not implemented**.

## 8. Decisions

Architecture decision records live in
[docs/architecture/decision-records/](docs/architecture/decision-records/).
