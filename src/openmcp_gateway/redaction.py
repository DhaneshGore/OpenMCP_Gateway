"""Secret redaction for logs and captured upstream output."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable

REDACTED = "[REDACTED]"

# Generic patterns. They are a safety net; exact-value redaction of known
# secrets (registered below) is the primary mechanism.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(authorization)\s*[:=]\s*\S+(\s+\S+)?"),
    re.compile(
        r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|access[_-]?token|secret|password|passwd|token))"
        r"(\"?\s*[:=]\s*\"?)([^\s\"',}]{4,})"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,})\b"),
)

_MIN_SECRET_LEN = 6


class Redactor:
    """Replaces registered secret values and common credential patterns."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: frozenset[str] = frozenset()

    def register(self, values: Iterable[str]) -> None:
        new = {v for v in values if v and len(v) >= _MIN_SECRET_LEN}
        if not new:
            return
        with self._lock:
            self._secrets = self._secrets | new

    def redact(self, text: str) -> str:
        if not text:
            return text
        # Longest first so a secret containing another is fully replaced.
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret in text:
                text = text.replace(secret, REDACTED)
        text = _PATTERNS[0].sub(lambda m: f"{m.group(1)} {REDACTED}", text)
        text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}: {REDACTED}", text)
        text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        text = _PATTERNS[3].sub(REDACTED, text)
        return text


# Process-wide instance used by the logging filter and stderr capture.
redactor = Redactor()
