from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from notes.validation import validate

DATA = Path(os.environ.get("OPENMCP_PACKAGE_DATA", ".")) / "notes.json"


class NoteStore:
    def _load(self) -> dict[str, dict[str, str]]:
        return json.loads(DATA.read_text()) if DATA.exists() else {}

    def add(self, title: str, body: str) -> str:
        notes = self._load()
        note_id = uuid.uuid4().hex[:8]
        note = {"id": note_id, "title": title, "body": body}
        validate(note)
        notes[note_id] = note
        DATA.write_text(json.dumps(notes))
        return note_id

    def get(self, note_id: str) -> dict[str, str]:
        return self._load()[note_id]

    def all(self) -> list[dict[str, str]]:
        return list(self._load().values())
