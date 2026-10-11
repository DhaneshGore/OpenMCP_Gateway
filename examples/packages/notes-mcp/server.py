"""notes-mcp: an example multi-file MCP server package."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from notes.formatting import render
from notes.store import NoteStore

mcp = MCPServer("notes-mcp", instructions="Keep short notes for the current session.")
store = NoteStore()


@mcp.tool()
def add_note(title: str, body: str) -> str:
    """Save a note and return its id."""
    return store.add(title, body)


@mcp.tool()
def list_notes() -> list[str]:
    """Titles of all notes."""
    return [n["title"] for n in store.all()]


@mcp.tool()
def show_note(note_id: str) -> str:
    """Render one note as Markdown."""
    return render(store.get(note_id))


if __name__ == "__main__":
    mcp.run("stdio")
