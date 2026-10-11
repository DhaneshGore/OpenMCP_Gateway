# Getting started

This guide takes you from nothing to an authenticated gateway serving your own
MCP servers. It assumes Linux or macOS and Python 3.11+. Windows is untested.

## 1. Install

```bash
git clone https://github.com/DhaneshGore/mcp-bridge.git
cd mcp-bridge
python -m venv .venv
. .venv/bin/activate
pip install .
openmcp --version
```

There is no PyPI release yet. For development, install with
`pip install -e ".[dev]"` instead (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## 2. Run the demo

The repository ships a tiny demo MCP server (`openmcp_gateway.demo.echo_server`)
and a config that launches it:

```bash
openmcp --config examples/openmcp.yaml doctor
openmcp --config examples/openmcp.yaml serve
```

`doctor` validates the configuration, launches every enabled server once,
performs the MCP handshake and reports the protocol version and tool count:

```text
ok   config: examples/openmcp.yaml
ok   server demo: started, protocol 2025-11-25, 3 tool(s)
no problems found
```

`serve` listens on `127.0.0.1:8080`. In another terminal:

```bash
curl -s localhost:8080/health      # {"status":"ok"}
curl -s localhost:8080/ready       # {"status":"ready","servers":{"expected":1,"ready":1}}
```

## 3. Connect a client

The MCP endpoint is **`http://127.0.0.1:8080/mcp`** (Streamable HTTP). Any
client that supports Streamable HTTP can connect, for example the official
Python SDK:

```python
import asyncio
from mcp import Client


async def main():
    async with Client("http://127.0.0.1:8080/mcp") as client:
        for tool in (await client.list_tools()).tools:
            print(tool.name)
        print((await client.call_tool("demo__echo", {"text": "hi"})).content[0].text)


asyncio.run(main())
```

Tool names are namespaced as `<server id>__<tool name>`. See
[transports.md](transports.md#namespacing).

## 4. Add your own server

Edit a copy of the config and add entries under `servers:`. A stdio server is
a command plus arguments. It is executed directly, never through a shell:

```yaml
servers:
  - id: files
    description: Read-only access to one directory
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/srv/shared"]
    inherit_env: ["NODE_OPTIONS"]       # pass through extra variables explicitly
```

> The gateway does **not** pass its own environment to servers. A child gets
> `PATH`, `HOME`, locale variables, and only what you list. Use
> `env_from_host` for secrets so their values are redacted from logs.

```yaml
  - id: github
    command: github-mcp-server
    args: ["stdio"]
    env_from_host:
      GITHUB_PERSONAL_ACCESS_TOKEN: GITHUB_TOKEN   # child var: gateway var
```

A remote Streamable HTTP server:

```yaml
  - id: search
    transport: http
    url: https://mcp.example.com/mcp
    headers_from_host:
      Authorization: SEARCH_API_AUTH   # e.g. "Bearer …" kept in an env var
```

Run `openmcp --config your.yaml config validate` after every edit.

## 5. Turn on authentication

Anything beyond your own laptop needs tokens. Generate one per client:

```bash
openmcp token generate --name laptop --server files --server github
```

The command prints the token once and a config entry containing only its
SHA-256 hash:

```yaml
auth:
  mode: bearer
  tokens:
    - {"name": "laptop", "sha256": "…", "servers": ["files", "github"]}
```

Clients send `Authorization: Bearer <token>`. A principal sees and calls only
the servers in its `servers` list (`"*"` means all).

## 6. Use the admin API and CLI

Create an admin token with `openmcp token generate --name ops --admin`, add
it to `auth.tokens`, restart, then:

```bash
export OPENMCP_ADMIN_TOKEN=<the token>
openmcp --config your.yaml server list
openmcp --config your.yaml server tools files
openmcp --config your.yaml server logs files --limit 50
openmcp --config your.yaml server restart files
```

The admin API always requires a token with the `admin` role, even when MCP
traffic is unauthenticated. Its OpenAPI schema is at `/openapi.json`.

## Next steps

* [configuration.md](configuration.md): every option
* [security.md](security.md): read before exposing the gateway to a network
* [deployment.md](deployment.md): Docker and reverse proxies
