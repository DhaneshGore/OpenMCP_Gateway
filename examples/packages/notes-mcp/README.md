# notes-mcp (example package)

A small multi-file MCP server package. Upload it to a gateway with packages
enabled:

```bash
openmcp package validate examples/packages/notes-mcp
openmcp package upload examples/packages/notes-mcp
openmcp package deploy notes-mcp 0.1.0 --id notes
```

Notes are stored in the instance's private data directory
(`$OPENMCP_PACKAGE_DATA`), which is removed when the server stops.
