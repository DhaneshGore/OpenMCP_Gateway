# DRAFT: launch announcement

> Not for publication without maintainer approval. Verify every claim against
> feature-status.md on the day of posting.

**OpenMCP Gateway: an open-source front door for your MCP servers**

If you use the Model Context Protocol, you probably have a handful of MCP
servers: a filesystem server, a GitHub server, something internal. Each one
needs to be launched, configured, secured and watched, separately, for every
client.

OpenMCP Gateway is a small, independent, Apache-2.0 project that puts all of
them behind one Streamable HTTP endpoint:

- **Runs local (stdio) and remote (Streamable HTTP) MCP servers** and exposes
  their tools, prompts and resources with collision-free names.
- **Secure by default.** It listens on localhost unless you configure tokens,
  gives each client its own server allowlist, doesn't pass the gateway's
  environment to servers, and checks outbound connections to remote servers
  against SSRF and DNS-rebinding attacks.
- **Built to fail predictably.** Responses are matched by id, never by order;
  timeouts and cancellations propagate; crashed servers restart with backoff
  and a hard cap; every queue and buffer is bounded.
- **Operable.** Health/readiness probes, Prometheus metrics, JSON logs with
  secret redaction, an admin API and CLI, and a `doctor` command.

It's built on the official MCP Python SDK and works with any MCP client that
speaks Streamable HTTP. It's not tied to any model provider or agent
framework.

It's early (0.x). We're looking for feedback and contributors, especially on
OAuth support, resource templates, and running real-world servers through it.

→ Quick start, docs and good first issues: https://github.com/DhaneshGore/mcp-bridge
