from __future__ import annotations

from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parent.parent / "templates" / "note.md").read_text()


def render(note: dict[str, str]) -> str:
    return TEMPLATE.format(**note)
