"""Helpers for package tests: build ZIPs, variants and wheels."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any

from openmcp_gateway.config import PackageLimits
from openmcp_gateway.packages.archive import PackagePolicy

FIXTURES = Path(__file__).parent / "fixtures" / "packages"
WEATHER = FIXTURES / "weather-mcp"

DEFAULT_POLICY = PackagePolicy(
    limits=PackageLimits(),
    allowed_extensions=frozenset([".py", ".json", ".yaml", ".yml", ".txt", ".md", ".lock", ".toml", ".cfg"]),
    allowed_filenames=frozenset(["LICENSE", "README"]),
)


def zip_dir(
    root: Path, prefix: str = "", *, reverse: bool = False, method: int = zipfile.ZIP_DEFLATED
) -> bytes:
    files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    if reverse:
        files.reverse()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=method) as zf:
        for path in files:
            zf.write(path, prefix + path.relative_to(root).as_posix())
    return out.getvalue()


def zip_entries(entries: dict[str, bytes | str], *, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=method) as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return out.getvalue()


def weather_entries(**overrides: bytes | str | None) -> dict[str, bytes | str]:
    """All fixture files as a dict, with optional replacements (None removes a file)."""
    entries: dict[str, bytes | str] = {
        p.relative_to(WEATHER).as_posix(): p.read_bytes()
        for p in sorted(WEATHER.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    for name, body in overrides.items():
        if body is None:
            entries.pop(name, None)
        else:
            entries[name] = body
    return entries


def metadata(**fields: Any) -> str:
    base = json.loads((WEATHER / "metadata.json").read_text())
    base.update(fields)
    return json.dumps(base)


def weather_variant(tmp: Path, version: str, marker: str | None = None) -> Path:
    """A copy of the fixture with another version (and optionally changed code)."""
    dest = tmp / f"weather-{version}"
    shutil.copytree(WEATHER, dest, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "metadata.json").write_text(metadata(version=version))
    if marker:
        (dest / "utils" / "marker.py").write_text(f"MARKER = {marker!r}\n")
    return dest


def symlink_zip(name: str, target: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("metadata.json", metadata())
        zf.writestr("server.py", "print('x')\n")
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, target)
    return out.getvalue()


def build_wheel(dest_dir: Path, name: str = "tinydep", version: str = "1.0.0", value: str = "tiny") -> Path:
    """Build a minimal pure-Python wheel deterministically (no build backend needed)."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f"VALUE = {value!r}\n".encode(),
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record_lines = []
    for path, body in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
        record_lines.append(f"{path},sha256={digest},{len(body)}")
    record_lines.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(record_lines) + "\n").encode()
    wheel = dest_dir / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, body in files.items():
            info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
            zf.writestr(info, body)
    return wheel


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
