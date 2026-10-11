"""Lock-file parsing and dependency policy checks.

Only exact, hash-pinned requirements are accepted (`name==version` plus at
least one `--hash=sha256:`), so an installation is reproducible and pip's
`--require-hashes` mode verifies every downloaded file. Index options inside
the lock are rejected: the package index is chosen by the operator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_REQ = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9,._ -]*\])?"
    r"==(?P<version>[A-Za-z0-9.+!_-]+)"
    r"\s*(?:;(?P<marker>[^-][^\n]*?))?"
    r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-f]{64})+)\s*$"
)
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


@dataclass(frozen=True)
class LockedRequirement:
    name: str
    version: str
    hashes: tuple[str, ...]


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _logical_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    buf = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0] if not raw.lstrip().startswith("#") else ""
        if not buf:
            start = number
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        buf += line
        if buf.strip():
            out.append((start, " ".join(buf.split())))
        buf = ""
    if buf.strip():
        out.append((start, " ".join(buf.split())))
    return out


def parse_lock(text: str) -> tuple[list[LockedRequirement], list[str]]:
    """Return (requirements, errors)."""
    reqs: list[LockedRequirement] = []
    errors: list[str] = []
    seen: set[str] = set()
    for number, line in _logical_lines(text):
        match = _REQ.match(line)
        if match is None:
            if line.startswith("-"):
                errors.append(f"lock line {number}: options are not allowed ({line.split()[0]})")
            else:
                errors.append(f"lock line {number}: expected 'name==version --hash=sha256:<64 hex>'")
            continue
        name = normalize_name(match["name"])
        if name in seen:
            errors.append(f"lock line {number}: duplicate requirement {name}")
            continue
        seen.add(name)
        reqs.append(LockedRequirement(name, match["version"], tuple(_HASH.findall(match["hashes"]))))
    return reqs, errors


def has_requirements(text: str) -> bool:
    """True if a requirements file contains anything besides comments/blank lines."""
    return bool(_logical_lines(text))
