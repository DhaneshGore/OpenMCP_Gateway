# Feature status and known limitations

Snapshot for the first public release candidate (version `0.1.0.dev0`,
2026-10-11). "Implemented" means the code exists and automated tests cover it.

## Implemented

| Feature | Tests |
| --- | --- |
| Streamable HTTP endpoint (SDK), handshake era + 2026-07-28 | `tests/e2e/test_gateway_http.py` |
| stdio upstreams with id-correlated multiplexer | `tests/unit/test_jsonrpc.py`, `tests/integration/test_stdio_upstream.py` |
| Remote Streamable HTTP upstreams | `tests/integration/test_remote_upstream.py` |
| Tools, prompts, resources forwarding; namespacing | e2e + `tests/unit/test_namespace.py` |
| Progress relay (stdio and HTTP upstreams) | e2e, remote |
| Cancellation and timeouts → `notifications/cancelled` upstream | unit + integration |
| Lifecycle: single-flight start, restart backoff/cap, lazy start, operator hold | `tests/integration/test_manager.py` |
| Concurrency limits, bounded queues | `tests/integration/test_manager.py` |
| Bearer tokens, per-server authorization, session binding | `tests/e2e/test_gateway_http.py` |
| Admin API, roles, metrics, CLI | `tests/e2e/test_admin_api.py`, `tests/e2e/test_cli.py` |
| Rate limiting (in-process) | `tests/unit/test_ratelimit.py`, e2e |
| SSRF guard at connect time | `tests/unit/test_ssrf_policy.py`, `tests/integration/test_remote_upstream.py` |
| Secret redaction (logs, stderr capture, admin output) | unit + e2e |
| SQLite registry with migrations | `tests/unit/test_storage.py`, `tests/e2e/test_registry.py` |
| Docker image (non-root, read-only rootfs) | CI `docker` job; verified manually |
| Multi-file packages: validation, immutable versions, integrity checks | `tests/unit/test_package_archive.py`, `tests/integration/test_package_service.py` |
| Package storage: local + S3-compatible artifacts; SQLite + MongoDB catalog | integration (S3 via moto; MongoDB in CI `services` job) |
| Package runtime: nested imports, resources, two versions, crash cleanup, Landlock isolation, network deny, locked dependencies | `tests/e2e/test_packages_e2e.py` (isolation tests skip without Landlock) |

## Experimental

* OpenTelemetry spans: emitted through the API; no built-in exporter
  configuration. Not covered by automated tests.
* Load-test suite: correctness under sustained concurrency; numbers are
  machine-specific and not published.

## Planned (not implemented)

OAuth 2.1 / OIDC authentication; resource templates, subscriptions and
completions; sampling/elicitation/roots proxying; `list_changed`
notifications to clients; 2026-07-28 toward stdio upstreams; legacy HTTP+SSE
upstreams; distributed rate limits and shared sessions; persistent audit log;
PostgreSQL storage. See [ROADMAP.md](../../ROADMAP.md).

## Known limitations

* **Single-process state.** Sessions, limits and upstream processes are per
  replica; multi-replica deployments need sticky sessions and multiply limits.
* **Upstream results are re-validated with the SDK's models.** Fields those
  models don't know are dropped from forwarded results.
* **Resources keep upstream URIs.** URIs offered by more than one visible
  server are hidden as ambiguous. Templated URIs cannot be read yet.
* **Every list request may start lazily started servers.** Servers with
  `autostart: false` are started by the first request that needs them,
  including listings.
* **No sandboxing of stdio servers.** They run as the gateway's OS user.
* **Pattern-based redaction is best effort.** Exact-value redaction covers
  configured secrets only.
* **Token changes require a restart.** Tokens are read from the config file
  at startup.
* **HTTP-layer throughput has not been optimized.** In a single-process
  local test (client and gateway in one event loop), the HTTP path was much
  slower than the stdio multiplexer on its own. Profiling is future work.
* **Platforms.** Developed and tested on Linux (Python 3.11 and 3.14 locally,
  3.11–3.14 in CI). macOS runs in CI on one Python version. Windows is
  untested.
* **Tool name length.** Namespaced names may exceed 128 characters (logged).
* **Package isolation is defence in depth, not a security boundary.** It
  needs Linux Landlock and shares the gateway's UID. Signals are isolated only
  on Linux 6.12+. With `network: allow`, packages can reach cloud metadata,
  `localhost` services and the gateway's own endpoint. There are no CPU or
  memory quotas by default, and UDP is not restricted. The package cache is
  not garbage-collected, and archives (up to the configured limits) are
  processed in memory. See docs/packages.md.
* **No SSH transport.** Remote MCP servers are reachable over Streamable
  HTTP only; SSH-based execution is not implemented.
* **No native TLS listener.** Serve HTTPS through a reverse proxy. Remote
  upstreams can use HTTPS, but there is no option to trust a private CA other
  than disabling verification.
* **httpx2 internals.** The SSRF guard replaces the HTTP transport's
  connection pool; a test fails if an upgrade breaks that hook.
