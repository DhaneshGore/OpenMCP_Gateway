"""Validation, canonicalisation and safe extraction of package archives.

Uploads are untrusted. Nothing in this module executes package code: Python
sources are only *compiled* to detect syntax errors. Accepted packages are
re-packed into a canonical archive (sorted entries, fixed timestamps, no
compression) so the SHA-256 of the artifact identifies the content no matter
which tool produced the upload.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import unicodedata
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from openmcp_gateway.config import PackageLimits
from openmcp_gateway.packages.dependencies import has_requirements, normalize_name, parse_lock
from openmcp_gateway.packages.metadata import MAX_METADATA_BYTES, METADATA_FILE, PackageFile, PackageMetadata

DEFAULT_REQUIREMENTS = "requirements.txt"
DEFAULT_LOCK = "requirements.lock"
# Ratio checks only apply above this size; tiny files legitimately compress well.
_RATIO_FLOOR = 64 * 1024
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_READ_CHUNK = 64 * 1024


class PackageValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class IntegrityError(Exception):
    """A stored artifact or extracted file does not match its recorded digest."""


@dataclass(frozen=True)
class PackagePolicy:
    limits: PackageLimits
    allowed_extensions: frozenset[str]
    allowed_filenames: frozenset[str]
    dependency_policy: str = "locked"  # "none" | "locked"
    allowed_projects: frozenset[str] = frozenset()


@dataclass
class ValidatedPackage:
    metadata: PackageMetadata
    files: list[PackageFile]
    contents: dict[str, bytes]
    artifact: bytes
    digest: str
    lock_digest: str | None
    warnings: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.artifact)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------- paths


def _check_entry_name(name: str, limits: PackageLimits) -> str | None:
    """Return an error string, or None if the name is a safe relative path."""
    if not name:
        return "empty entry name"
    if "\x00" in name or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return f"control characters in path {name!r}"
    if "\\" in name:
        return f"backslash in path {name!r}"
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return f"absolute path {name!r}"
    parts = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return f"path traversal or empty component in {name!r}"
    if len(parts) > limits.max_path_depth:
        return f"path too deep ({len(parts)} > {limits.max_path_depth}): {name!r}"
    if len(name.encode("utf-8")) > limits.max_path_length:
        return f"path too long: {name[:60]!r}…"
    return None


def _entry_kind(info: zipfile.ZipInfo) -> str:
    mode = info.external_attr >> 16
    if info.is_dir():
        if mode and stat.S_IFMT(mode) not in (0, stat.S_IFDIR):
            return "special"
        return "dir"
    if mode == 0 or stat.S_IFMT(mode) in (0, stat.S_IFREG):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _strip_common_prefix(paths: list[str]) -> str:
    """If every entry lives under one top-level directory holding metadata.json, return it."""
    if not paths or any("/" not in p for p in paths):
        return ""
    first = {p.split("/", 1)[0] for p in paths}
    if len(first) != 1:
        return ""
    prefix = next(iter(first)) + "/"
    return prefix if prefix + METADATA_FILE in paths else ""


# --------------------------------------------------------------------- inspect


def inspect_archive(data: bytes, policy: PackagePolicy) -> ValidatedPackage:
    """Validate an uploaded ZIP and build the canonical artifact. Raises PackageValidationError."""
    limits = policy.limits
    errors: list[str] = []
    if len(data) > limits.max_archive_bytes:
        raise PackageValidationError([f"archive is larger than {limits.max_archive_bytes} bytes"])
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError):
        raise PackageValidationError(["not a valid ZIP archive"]) from None

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_files * 2:
            raise PackageValidationError([f"archive has too many entries ({len(infos)})"])

        file_infos: list[zipfile.ZipInfo] = []
        declared_total = 0
        for info in infos:
            name = info.filename
            problem = _check_entry_name(name, limits)
            if problem:
                errors.append(problem)
                continue
            kind = _entry_kind(info)
            if kind == "symlink":
                errors.append(f"symbolic links are not allowed: {name!r}")
                continue
            if kind == "special":
                errors.append(f"special files are not allowed: {name!r}")
                continue
            if info.flag_bits & 0x1:
                errors.append(f"encrypted entries are not allowed: {name!r}")
                continue
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                errors.append(f"unsupported compression method for {name!r}")
                continue
            if kind == "dir":
                continue
            if info.file_size > limits.max_file_bytes:
                errors.append(f"file too large ({info.file_size} bytes): {name!r}")
                continue
            if info.file_size > _RATIO_FLOOR and info.file_size > limits.max_compression_ratio * max(
                info.compress_size, 1
            ):
                errors.append(f"suspicious compression ratio for {name!r}")
                continue
            declared_total += info.file_size
            file_infos.append(info)

        if len(file_infos) > limits.max_files:
            errors.append(f"too many files ({len(file_infos)} > {limits.max_files})")
        if declared_total > limits.max_extracted_bytes:
            errors.append(f"extracted size {declared_total} exceeds {limits.max_extracted_bytes} bytes")
        compressed_total = sum(i.compress_size for i in file_infos)
        if declared_total > _RATIO_FLOOR and declared_total > limits.max_compression_ratio * max(
            compressed_total, 1
        ):
            errors.append("suspicious overall compression ratio")
        if errors:
            raise PackageValidationError(errors)

        # Duplicates (exact, Unicode-normalised and case-insensitive collisions).
        seen: dict[str, str] = {}
        for info in file_infos:
            key = unicodedata.normalize("NFC", info.filename).casefold()
            if key in seen:
                errors.append(f"duplicate path {info.filename!r} (collides with {seen[key]!r})")
            else:
                seen[key] = info.filename
        if errors:
            raise PackageValidationError(errors)

        # Read with hard limits: never trust the sizes declared in headers.
        raw: dict[str, bytes] = {}
        actual_total = 0
        for info in file_infos:
            buf = bytearray()
            try:
                with zf.open(info) as fh:
                    while chunk := fh.read(_READ_CHUNK):
                        buf += chunk
                        if len(buf) > info.file_size or len(buf) > limits.max_file_bytes:
                            raise PackageValidationError([f"entry {info.filename!r} is larger than declared"])
            except (zipfile.BadZipFile, OSError, EOFError, ValueError):
                raise PackageValidationError([f"corrupt entry {info.filename!r}"]) from None
            actual_total += len(buf)
            if actual_total > limits.max_extracted_bytes:
                raise PackageValidationError(["extracted size exceeds the limit"])
            raw[info.filename] = bytes(buf)

    prefix = _strip_common_prefix(list(raw))
    contents = {name[len(prefix) :]: body for name, body in raw.items()}
    return _validate_contents(contents, policy)


def _allowed(path: str, policy: PackagePolicy) -> bool:
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    return name in policy.allowed_filenames or (bool(suffix) and suffix in policy.allowed_extensions)


def _validate_contents(contents: dict[str, bytes], policy: PackagePolicy) -> ValidatedPackage:
    errors: list[str] = []
    warnings: list[str] = []

    for path in sorted(contents):
        if not _allowed(path, policy):
            errors.append(f"file type not permitted: {path!r}")

    meta_bytes = contents.get(METADATA_FILE)
    metadata: PackageMetadata | None = None
    if meta_bytes is None:
        errors.append(f"{METADATA_FILE} is missing at the package root")
    elif len(meta_bytes) > MAX_METADATA_BYTES:
        errors.append(f"{METADATA_FILE} is too large")
    else:
        try:
            metadata = PackageMetadata.model_validate(json.loads(meta_bytes.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"{METADATA_FILE} is not valid UTF-8 JSON")
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"]) or "(root)"
                errors.append(f"{METADATA_FILE}: {loc}: {err['msg'].removeprefix('Value error, ')}")

    lock_digest: str | None = None
    if metadata is not None:
        if metadata.entrypoint not in contents:
            errors.append(f"entrypoint {metadata.entrypoint!r} does not exist in the package")
        requirements = metadata.requirements or (
            DEFAULT_REQUIREMENTS if DEFAULT_REQUIREMENTS in contents else None
        )
        lock = metadata.lock or (DEFAULT_LOCK if DEFAULT_LOCK in contents else None)
        for declared in (metadata.requirements, metadata.lock):
            if declared and declared not in contents:
                errors.append(f"declared dependency file {declared!r} does not exist")
        req_text = _text(contents.get(requirements)) if requirements else None
        lock_text = _text(contents.get(lock)) if lock else None
        wants_deps = bool(req_text and has_requirements(req_text)) or bool(
            lock_text and has_requirements(lock_text)
        )
        if wants_deps and policy.dependency_policy == "none":
            errors.append(
                "this gateway does not install package dependencies (packages.dependencies.policy=none)"
            )
        elif wants_deps:
            if lock_text is None:
                errors.append(
                    f"dependencies must be pinned with hashes in a lock file ({DEFAULT_LOCK}); "
                    "generate one with `pip-compile --generate-hashes` or `uv pip compile --generate-hashes`"
                )
            else:
                reqs, lock_errors = parse_lock(lock_text)
                errors.extend(lock_errors)
                if policy.allowed_projects:
                    for req in reqs:
                        if req.name not in policy.allowed_projects:
                            errors.append(f"dependency {req.name!r} is not in the allowed project list")
                if not reqs and not lock_errors:
                    warnings.append("lock file declares no requirements")
                lock_digest = sha256_hex(lock_text.encode())

    for path, body in contents.items():
        if path.endswith(".py"):
            try:
                compile(body, path, "exec", dont_inherit=True)  # compiled, never executed
            except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
                line = getattr(exc, "lineno", None)
                errors.append(f"syntax error in {path!r}" + (f" at line {line}" if line else ""))

    if errors or metadata is None:
        raise PackageValidationError(errors or ["invalid package"])

    files = [PackageFile(path=p, size=len(b), sha256=sha256_hex(b)) for p, b in sorted(contents.items())]
    artifact = build_canonical_archive(contents)
    return ValidatedPackage(
        metadata=metadata,
        files=files,
        contents=contents,
        artifact=artifact,
        digest=sha256_hex(artifact),
        lock_digest=lock_digest,
        warnings=warnings,
    )


def _text(body: bytes | None) -> str | None:
    if body is None:
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def build_canonical_archive(contents: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in sorted(contents):
            info = zipfile.ZipInfo(path, date_time=_FIXED_DATE)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.create_system = 3
            zf.writestr(info, contents[path])
    return out.getvalue()


# --------------------------------------------------------------------- directories


def archive_directory(root: Path, limits: PackageLimits) -> bytes:
    """Zip a local package directory without following symlinks (trusted development use)."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise PackageValidationError([f"{root} is not a directory"])
    errors: list[str] = []
    entries: list[tuple[str, Path]] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        for d in list(dirnames):
            if d in ("__pycache__", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"):
                dirnames.remove(d)
            elif (current / d).is_symlink():
                errors.append(f"symbolic links are not allowed: {(current / d).relative_to(root)}")
                dirnames.remove(d)
        for name in filenames:
            path = current / name
            rel = path.relative_to(root).as_posix()
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                errors.append(f"symbolic links are not allowed: {rel}")
                continue
            if not stat.S_ISREG(st.st_mode):
                errors.append(f"special files are not allowed: {rel}")
                continue
            total += st.st_size
            if total > limits.max_extracted_bytes or len(entries) >= limits.max_files:
                raise PackageValidationError(["directory exceeds package size or file-count limits"])
            entries.append((rel, path))
    if errors:
        raise PackageValidationError(errors)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, path in sorted(entries):
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as fh:
                zf.writestr(rel, fh.read())
    return out.getvalue()


# --------------------------------------------------------------------- extraction


def extract_verified(
    artifact: Path,
    expected_digest: str,
    manifest: Iterable[PackageFile],
    dest: Path,
    policy: PackagePolicy,
) -> None:
    """Verify an artifact's digest, then extract it into an empty `dest`.

    Files are created exclusively, never through symlinks, and left read-only.
    Every file is checked against the manifest recorded at registration.
    """
    actual = sha256_file(artifact)
    if actual != expected_digest:
        raise IntegrityError(
            f"artifact checksum mismatch (expected {expected_digest[:12]}…, got {actual[:12]}…)"
        )
    try:
        package = inspect_archive(artifact.read_bytes(), policy)
    except PackageValidationError as exc:
        raise IntegrityError(f"stored artifact failed validation: {exc}") from None
    expected = {f.path: f for f in manifest}
    if set(expected) != set(package.contents):
        raise IntegrityError("artifact file list does not match the recorded manifest")

    dest.mkdir(mode=0o700, parents=False, exist_ok=False)
    root = dest.resolve()
    made_dirs: list[Path] = []
    for rel, body in sorted(package.contents.items()):
        if sha256_hex(body) != expected[rel].sha256:
            raise IntegrityError(f"file {rel!r} does not match the recorded checksum")
        target = root.joinpath(*rel.split("/"))
        if root not in target.parents:
            raise IntegrityError(f"refusing to write outside the package directory: {rel!r}")
        parent = target.parent
        for ancestor in reversed([parent, *parent.parents]):
            if ancestor == root or root not in ancestor.parents:
                continue
            if not ancestor.exists():
                ancestor.mkdir(mode=0o700)
                made_dirs.append(ancestor)
            elif ancestor.is_symlink() or not ancestor.is_dir():
                raise IntegrityError(f"unexpected filesystem object at {ancestor}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.chmod(target, 0o444)
    for directory in sorted(made_dirs, key=lambda p: len(p.parts), reverse=True):
        os.chmod(directory, 0o555)  # noqa: S103 - read-only for everyone; the tree is in a 0700 parent
    os.chmod(root, 0o555)  # noqa: S103


def policy_from_config(cfg: object) -> PackagePolicy:
    from openmcp_gateway.config import PackagesConfig

    assert isinstance(cfg, PackagesConfig)
    return PackagePolicy(
        limits=cfg.limits,
        allowed_extensions=frozenset(cfg.allowed_extensions),
        allowed_filenames=frozenset(cfg.allowed_filenames),
        dependency_policy=cfg.dependencies.policy,
        allowed_projects=frozenset(normalize_name(p) for p in cfg.dependencies.allowed_projects),
    )
