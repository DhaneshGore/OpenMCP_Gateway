# Demo scenarios

Each demo uses only implemented features. Commands assume a checkout with
`pip install .` done.

1. **Two-minute quick start.** `openmcp --config examples/openmcp.yaml serve`,
   then list and call `demo__add` from the Python SDK client (README).
2. **Many servers, one endpoint.** Configure the demo server plus a filesystem
   server (`npx @modelcontextprotocol/server-filesystem`); show namespaced
   tools and the `_meta` origin.
3. **Least privilege.** Two tokens: one limited to `files`, one with `*`.
   Show that the limited client never sees the other server's tools, and that
   calling one returns the same error as a non-existent tool.
4. **Crash recovery.** Kill a stdio server's process (`openmcp server
   inspect` shows the pid); show the `failed` state, backoff, automatic
   restart and the `openmcp_upstream_recent_failures` metric.
5. **Concurrency correctness.** Run `pytest -m load -s tests/load`: 2,000
   calls with randomized latency, every answer matched to its caller.
6. **SSRF guard.** Register a remote server whose URL points to
   `http://169.254.169.254/` or a hostname that resolves to it; show the
   `destination not allowed by egress policy` error.
7. **Operations.** Scrape `/admin/metrics` with a `metrics`-role token; view
   redacted stderr with `openmcp server logs`.
8. **Container.** `docker compose up --build`; show a non-root, read-only
   container serving MCP on localhost.
