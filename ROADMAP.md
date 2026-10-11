# Roadmap

Status markers: ✅ done · 🚧 in progress · ⏳ planned · 💭 under discussion.
Nothing here is a promise of dates; it is the order in which work is planned.

## MVP (0.1.0)

Goal: a developer can run one command, connect one local stdio MCP server, and
use its tools from any Streamable HTTP MCP client — safely and predictably.

| | Item |
| --- | --- |
| ✅ | Package, `openmcp` CLI, YAML config with validation and env overrides |
| ✅ | Streamable HTTP endpoint `/mcp` (SDK; handshake era + 2026-07-28) |
| ✅ | stdio upstreams with id-correlated multiplexing, timeouts, cancellation, crash handling |
| ✅ | Aggregated, namespaced `tools/list` and `tools/call` |
| ✅ | Lifecycle manager: single-flight start, restart backoff + cap, graceful stop |
| ✅ | `/health`, `/ready`, `/version` |
| ✅ | Static bearer tokens (hashed), per-principal server allowlists, secure bind defaults |
| ✅ | JSON logs with redaction; bounded stderr capture |
| ✅ | Unit, concurrency, security and real-subprocess end-to-end tests |
| ✅ | Docs, examples, Dockerfile (built and smoke-tested locally) |
| 🚧 | CI workflow: written and linted; first run happens when pushed to GitHub |

Acceptance criteria are listed in [docs/architecture/decision-records/0000-mvp-acceptance.md](docs/architecture/decision-records/0000-mvp-acceptance.md).

## v0.2 — breadth

| | Item |
| --- | --- |
| ✅ | Prompts and resources forwarding (namespaced) |
| ✅ | Progress notification relay |
| ✅ | Admin API (list/inspect/start/stop/restart/tools/logs/register/remove) + `openmcp server …` commands |
| ✅ | In-process rate limiting (global + per principal) |
| ✅ | Prometheus metrics endpoint |
| ✅ | Remote Streamable HTTP upstreams with SSRF guard |
| ✅ | SQLite-backed registry for servers added at runtime |

## v1.0 — production hardening

| | Item |
| --- | --- |
| ✅ | Multi-file server packages: validation, immutable versions, S3/local artifacts, SQLite/MongoDB catalog, locked dependencies, Landlock isolation |
| ⏳ | Package cache garbage collection |
| ⏳ | Per-package CPU/memory limits (cgroups) |
| ⏳ | OAuth 2.1 resource-server mode (token introspection / JWT validation) |
| ⏳ | Upstream 2026-07-28 (modern era) support for stdio servers |
| ⏳ | OpenTelemetry tracing exporter configuration |
| ⏳ | Audit log of admin actions (persistent) |
| ⏳ | `tools/list_changed` propagation to clients |
| ⏳ | Resource templates, subscriptions, completions |
| ⏳ | Profiling and optimization of the HTTP request path |
| ⏳ | Load-test suite with published, reproducible methodology |
| ⏳ | Stable extension interfaces (semver-covered) |

## Future

| | Item |
| --- | --- |
| 💭 | PostgreSQL storage adapter |
| 💭 | Distributed rate limiting / shared session state for multi-replica deployments |
| 💭 | Sampling / elicitation proxying from upstream to client |
| 💭 | Legacy HTTP+SSE upstream adapter (only if demand is shown) |
| 💭 | Per-tool policies (argument constraints, approval hooks) |

Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md) — items marked ⏳ are good
candidates for design discussions in issues before code.
