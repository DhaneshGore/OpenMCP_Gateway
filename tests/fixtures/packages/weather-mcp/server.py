"""weather-mcp: a multi-file MCP server package."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from tools.forecast import forecast_for
from tools.weather import current_conditions
from utils.helpers import PACKAGE_ROOT, load_schema, package_version, render_report

mcp = MCPServer("weather-mcp", instructions="Deterministic demo weather data.")


@mcp.tool()
def current(city: str) -> str:
    """Current conditions for a city."""
    return json.dumps(current_conditions(city), sort_keys=True)


@mcp.tool()
def forecast(city: str, days: int = 3) -> str:
    """A rendered multi-day forecast."""
    return render_report(city, forecast_for(city, days))


@mcp.tool()
def schema_title() -> str:
    """Title of the bundled JSON schema."""
    return load_schema()["title"]


@mcp.tool()
def version() -> str:
    """Package version, read from metadata.json at runtime."""
    return package_version()


@mcp.tool()
def whereami() -> str:
    """Runtime locations (used by isolation tests)."""
    return json.dumps(
        {
            "cwd": os.getcwd(),
            "root": os.environ.get("OPENMCP_PACKAGE_ROOT"),
            "data": os.environ.get("OPENMCP_PACKAGE_DATA"),
            "tmp": os.environ.get("TMPDIR"),
            "module_root": str(PACKAGE_ROOT),
            "pid": os.getpid(),
        }
    )


@mcp.tool()
def try_read(path: str) -> str:
    """Attempt to read any path (used by isolation tests)."""
    try:
        return "READ:" + Path(path).read_text()[:200]
    except Exception as exc:
        return f"DENIED:{type(exc).__name__}"


@mcp.tool()
def try_write(path: str) -> str:
    """Attempt to write a file (used by isolation tests)."""
    try:
        Path(path).write_text("written")
        return "WROTE"
    except Exception as exc:
        return f"DENIED:{type(exc).__name__}"


@mcp.tool()
def try_connect(host: str, port: int) -> str:
    """Attempt a TCP connection (used by network-isolation tests)."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return "CONNECTED"
    except Exception as exc:
        return f"DENIED:{type(exc).__name__}"


@mcp.resource("weather://report-template")
def report_template() -> str:
    """The raw report template shipped with the package."""
    return (PACKAGE_ROOT / "templates" / "report.txt").read_text()


if __name__ == "__main__":
    mcp.run("stdio")
