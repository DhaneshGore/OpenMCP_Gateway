from __future__ import annotations

import json
from pathlib import Path

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "note.json").read_text())


def validate(note: dict[str, str]) -> None:
    for field in SCHEMA["required"]:
        if not note.get(field):
            raise ValueError(f"note is missing {field!r}")
    if len(note["title"]) > SCHEMA["properties"]["title"]["maxLength"]:
        raise ValueError("title too long")
