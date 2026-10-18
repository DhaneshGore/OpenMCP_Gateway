# Deployment

## Local (no container)

```bash
pip install .
openmcp --config openmcp.yaml serve
```

`serve` runs one Uvicorn worker. Stop it with Ctrl-C or SIGTERM. The gateway
stops accepting requests, waits up to 10 s for in-flight HTTP requests, then
shuts down every upstream (close stdin → SIGTERM → SIGKILL per process group).

## Docker

```bash
docker build -t openmcp-gateway .
docker compose up --build        # local demo, see docker-compose.yml
```

The image:

* runs as an unprivileged user (uid 10001) with the virtualenv in `/opt/venv`;
* reads its config from `/etc/openmcp/openmcp.yaml` (`OPENMCP_CONFIG`);
* writes only to `/var/lib/openmcp` (SQLite registry) and `/tmp`, so it
  works with `read_only: true`;
* has a `HEALTHCHECK` against `/health`;
* includes the `s3`, `mongodb`, `jwt`, and `otel` optional extras — S3 and
  MongoDB storage backends, JWT/OIDC authentication, and OpenTelemetry export
  are available without a custom build;
* does **not** include `hot-reload` (`watchfiles` requires a Rust toolchain
  at build time); add `pip install "openmcp-gateway[hot-reload]"` to a derived
  image if needed.

The compose file publishes the port on `127.0.0.1` only and uses
[`examples/docker/openmcp.yaml`](../examples/docker/openmcp.yaml), which
disables authentication. That's acceptable only because of the loopback
port mapping. For anything reachable from a network, use bearer tokens
(see [`examples/production.yaml`](../examples/production.yaml)).

stdio servers run **inside** the gateway container, so the image must contain
their runtimes. The base image has Python only. For Node-based servers
(`npx …`), build a derived image:

```dockerfile
FROM openmcp-gateway:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
 && rm -rf /var/lib/apt/lists/*
USER openmcp
```

Pass secrets that servers need as environment variables
(`docker run -e GITHUB_TOKEN=…` or compose `env_file:`) and reference them
with `env_from_host`. Never bake secrets into the config file or image.

### Packages in containers

The image includes the S3 and MongoDB clients. With packages enabled, point
`packages.cache_dir`, `runtime_dir` (and the local artifact/catalog paths, if
used) at `/var/lib/openmcp`, the image's writable volume. Landlock works
under Docker's default seccomp profile, with a read-only root filesystem and
all capabilities dropped. This was verified with the project image, including
the cross-instance isolation checks.

## Behind a reverse proxy (TLS)

Terminate TLS at a proxy (nginx, Caddy, Envoy, a cloud load balancer) and
forward to the gateway:

* Set `gateway.allowed_hosts` to the public Host values, and
  `gateway.allowed_origins` if browser-based clients connect.
* Set `gateway.trusted_proxies` to the proxy's IP so `X-Forwarded-For` and
  `X-Forwarded-Proto` are honoured from it (and only from it).
* Disable response buffering for the MCP path: Streamable HTTP uses
  Server-Sent Events. For nginx: `proxy_buffering off;` and a generous
  `proxy_read_timeout`.
* Pass the `Authorization`, `Mcp-Session-Id`, `Mcp-Protocol-Version` and
  `Last-Event-ID` headers through unchanged.

## Probes and monitoring

| Endpoint | Auth | Use |
| --- | --- | --- |
| `GET /health` | none | liveness: the process is serving HTTP |
| `GET /ready` | none | readiness: startup finished; `status` is `ready`, `degraded` (some autostart servers not ready) or `not_ready`, plus counts. No server ids. With `gateway.ready_requires_all_servers: true`, returns 503 until every autostart server is ready |
| `GET /version` | none | build and protocol versions |
| `GET /admin/metrics` | `admin` or `metrics` role | Prometheus text format |

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: openmcp-gateway
    metrics_path: /admin/metrics
    authorization:
      credentials_file: /etc/prometheus/openmcp-metrics-token
    static_configs:
      - targets: ["gateway.internal:8080"]
```

Metrics include request counts and latency per MCP method and outcome,
upstream request counts and latency per server, server state, in-flight and
queued requests, recent failures, and low-level JSON-RPC counters (unmatched
responses, malformed messages, oversize messages, timeouts).

### Tracing (experimental)

The gateway emits OpenTelemetry spans (`upstream <method>`) through the
OpenTelemetry API, and the MCP SDK emits a span per handled message. With no
OpenTelemetry SDK installed, these are no-ops. To export them, install and
configure the OpenTelemetry SDK, for example with the
`opentelemetry-instrument` launcher from `opentelemetry-distro`, using the
standard `OTEL_*` environment variables. Built-in exporter configuration is on
the roadmap.

## Scaling and multiple replicas

All runtime state is **per process**: sessions, upstream processes and
connections, concurrency limits and rate-limit buckets. Consequences:

* Handshake-era clients (`Mcp-Session-Id`) must reach the replica that
  created their session. Use sticky routing on the `Mcp-Session-Id` header,
  or route each client to a fixed replica.
* Each replica launches its own copy of every stdio server.
* Rate limits and `max_sessions` apply per replica. N replicas allow up to
  N times the configured rate.
* The SQLite registry must not be shared between replicas. Use one file per
  replica, or define servers in the config file.

Distributed rate limiting, shared session state and a shared registry are
**not implemented**. A single replica with appropriately sized
`max_concurrency` is the supported topology today.
