# OpenMCP Gateway

**One access-controlled, observable front door for all your MCP servers.**

> **Alpha:** The project is under active development. Interfaces and
> configuration may change between 0.x releases. Breaking changes are
> documented in [CHANGELOG.md](CHANGELOG.md).

OpenMCP Gateway is an independent, community-driven gateway for the
[Model Context Protocol](https://modelcontextprotocol.io). It runs your local
(stdio) and remote (Streamable HTTP) MCP servers behind a single Streamable
HTTP endpoint, and adds what you need to run them for real: authentication,
per-client authorization, process supervision, limits, metrics and an admin
API. It doesn't depend on any LLM provider or agent framework.

```text
 MCP clients ──► /mcp ──► OpenMCP Gateway ──► github (stdio subprocess)
 (any SDK)               auth · routing      ├─► filesystem (stdio subprocess)
                         limits · metrics    └─► search (remote Streamable HTTP)
```

## Features

What is implemented today. Everything here is covered by automated tests;
see [ROADMAP.md](ROADMAP.md) for what is planned.

| Area | Implemented |
| --- | --- |
| Client-facing transport | Streamable HTTP via the official MCP Python SDK; protocol revisions 2024-11-05 through 2025-11-25 (initialize handshake) and 2026-07-28 |
| Upstream servers | stdio subprocesses (own multiplexer); remote Streamable HTTP (SDK client); uploaded multi-file **packages** |
| Packages | ZIP or directory upload, strict archive validation, immutable versions with SHA-256 digests, storage in a local directory or S3-compatible store (tested with an S3 emulator) with a SQLite or MongoDB (tested with 7.0) catalog, hash-locked dependencies, verified extraction into private runtime directories, Landlock + seccomp sandbox per server on Linux (defence in depth with [documented limits](docs/packages.md#limitations-read-before-running-untrusted-packages)) |
| MCP features forwarded | tools (list/call), prompts (list/get), resources (list/read), progress notifications |
| Routing | collision-free `server__tool` namespacing; original names kept in `_meta` |
| Concurrency | id-correlated multiplexing, out-of-order responses, per-server concurrency limits with bounded queues |
| Reliability | single-flight start, crash detection, restart with exponential backoff and a hard cap, startup/request/shutdown timeouts, process-group shutdown |
| Security | bearer tokens (hashed), per-principal server allowlists, sessions bound to their creator, loopback-by-default, minimal subprocess environment, secret redaction, SSRF guard checked at connect time for remote upstreams, Host/Origin validation, size limits |
| Operations | `/health`, `/ready`, `/version`, admin API, Prometheus metrics, JSON logs, `openmcp doctor` |
| Storage | servers registered through the admin API kept in memory or SQLite (versioned migrations) |

**Not implemented yet** (see the roadmap): OAuth 2.1 / OIDC authentication,
resource templates and subscriptions, sampling/elicitation/roots proxying,
`list_changed` notifications to clients, distributed rate limiting or shared
sessions across replicas, the 2026-07-28 protocol revision toward stdio
upstreams, a legacy HTTP+SSE upstream adapter, SSH-based remote servers, a
built-in TLS listener (use a reverse proxy for HTTPS), and trusting a private
CA for HTTPS upstreams.

**Package isolation is defence in depth, not a security boundary for hostile
code.** Package servers run as the gateway's own OS user. With
`network: allow` they can reach cloud metadata services, unauthenticated
services on `localhost`, and the gateway's own endpoint. CPU and memory limits
are off by default and are per-process only, and isolation exists only on
Linux. Read the
[limitations](docs/packages.md#limitations-read-before-running-untrusted-packages)
before running packages you did not write.

## Quick start

You need Python 3.11 or newer.

```bash
git clone https://github.com/DhaneshGore/mcp-bridge.git
cd mcp-bridge
python -m venv .venv && . .venv/bin/activate
pip install .

openmcp --config examples/openmcp.yaml doctor   # checks config, launches the demo server once
openmcp --config examples/openmcp.yaml serve    # listens on http://127.0.0.1:8080
```

Point any MCP client that supports Streamable HTTP at
`http://127.0.0.1:8080/mcp`. With the Python SDK:

```python
import asyncio
from mcp import Client


async def main():
    async with Client("http://127.0.0.1:8080/mcp") as client:
        tools = await client.list_tools()
        print([t.name for t in tools.tools])  # ['demo__echo', 'demo__add', ...]
        result = await client.call_tool("demo__add", {"a": 2, "b": 3})
        print(result.content[0].text)  # 5.0


asyncio.run(main())
```

Continue with [docs/getting-started.md](docs/getting-started.md): adding your
own servers, turning on authentication, and deploying.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Getting started](docs/getting-started.md) | install, first server, first client, tokens |
| [Configuration](docs/configuration.md) | every setting, with defaults |
| [Transports](docs/transports.md) | protocol versions, stdio and HTTP details, namespacing |
| [Packages](docs/packages.md) | multi-file server packages: format, storage, versions, isolation |
| [Security](docs/security.md) | threat model, trust boundaries, hardening checklist |
| [Deployment](docs/deployment.md) | Docker, reverse proxies, scaling limits |
| [Troubleshooting](docs/troubleshooting.md) | error codes and common problems |
| [Architecture](ARCHITECTURE.md) | design, module boundaries, decision records |

## How it compares

Several MCP gateways and proxies exist. We don't publish comparisons or
performance claims we can't reproduce. What we aim to be:

* **Independent and provider-neutral**: no dependency on a model vendor or a
  hosted control plane.
* **Correct first**: the protocol plumbing is built on the official SDK, and
  the stdio multiplexer is tested against adversarial servers (out-of-order,
  duplicate, late, malformed and oversized responses; crashes; stderr floods).
* **Safe defaults**: an insecure network configuration (non-loopback bind
  without authentication, plain-HTTP or private-address upstreams) has to be
  turned on explicitly.
* **Small and readable**: a codebase that a new contributor can follow end to
  end.

## Contributing

Contributions are welcome, from typo fixes to new transports. Start with
[CONTRIBUTING.md](CONTRIBUTING.md); look for issues labelled
`good first issue`. Please report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE). Third-party dependencies and their
licenses are listed in [docs/dependencies.md](docs/dependencies.md).

*Model Context Protocol and MCP are used here to describe protocol
compatibility; this project is not affiliated with or endorsed by the protocol's
maintainers.*
