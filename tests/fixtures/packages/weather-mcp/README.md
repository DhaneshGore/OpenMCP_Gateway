# weather-mcp (test fixture)

A deliberately multi-file MCP server: `server.py` imports tools from
`tools/`, which use helpers and a fake API client from `utils/`, and read a
JSON schema, a YAML config and a text template at runtime. Data is
deterministic and offline.
