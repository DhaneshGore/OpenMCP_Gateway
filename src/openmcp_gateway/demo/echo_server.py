"""A tiny MCP server for demos and end-to-end tests (stdio transport).

Run directly with ``python -m openmcp_gateway.demo.echo_server``. It is built
with the official SDK, so it doubles as an independent implementation the
gateway is tested against.
"""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo-echo", instructions="Demo tools for trying out the gateway.")


@mcp.tool()
def echo(text: str) -> str:
    """Return the input text unchanged."""
    return text


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


@mcp.tool()
async def sleep_then_echo(text: str, seconds: float = 0.1) -> str:
    """Wait (max 30 s), then echo. Useful for observing concurrency and timeouts."""
    await asyncio.sleep(max(0.0, min(seconds, 30.0)))
    return text


@mcp.prompt()
def greet(name: str) -> str:
    """A greeting prompt."""
    return f"Say hello to {name}."


@mcp.resource("demo://readme")
def readme() -> str:
    """A static demo resource."""
    return "This resource is served by the demo echo server."


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
