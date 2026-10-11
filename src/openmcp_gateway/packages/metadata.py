"""Package metadata (`metadata.json`) and catalog records."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openmcp_gateway.config import PACKAGE_NAME_RE, PACKAGE_VERSION_RE

METADATA_FILE = "metadata.json"
MAX_METADATA_BYTES = 64 * 1024
_SAFE_REL_PATH = re.compile(r"^(?!/)(?!.*(^|/)\.\.?(/|$))[A-Za-z0-9_.\-/]+$")


def check_relative_path(value: str, what: str) -> str:
    if not _SAFE_REL_PATH.match(value) or "//" in value or value.endswith("/"):
        raise ValueError(f"{what} must be a relative path inside the package, got {value!r}")
    return value


class PackageMetadata(BaseModel):
    """Schema of a package's `metadata.json`."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Package name, ^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(description="Semantic version; immutable once registered.")
    description: str = Field("", max_length=2000)
    entrypoint: str = Field("server.py", description="Python file run as __main__.")
    args: list[str] = Field(default_factory=list, max_length=64, description="Default arguments.")
    requirements: str | None = Field(None, description="Human-readable requirements file.")
    lock: str | None = Field(None, description="Hash-pinned lock file used for installation.")
    license: str | None = Field(None, max_length=200)
    authors: list[str] = Field(default_factory=list, max_length=50)
    homepage: str | None = Field(None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not PACKAGE_NAME_RE.match(v):
            raise ValueError("name must match ^[a-z0-9][a-z0-9-]{0,63}$")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if not PACKAGE_VERSION_RE.match(v):
            raise ValueError("version must be a semantic version such as 1.2.0")
        return v

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint(cls, v: str) -> str:
        check_relative_path(v, "entrypoint")
        if not v.endswith(".py"):
            raise ValueError("entrypoint must be a .py file")
        return v

    @field_validator("requirements", "lock")
    @classmethod
    def _dep_files(cls, v: str | None) -> str | None:
        return None if v is None else check_relative_path(v, "dependency file")

    @field_validator("args")
    @classmethod
    def _args(cls, v: list[str]) -> list[str]:
        if any("\x00" in a for a in v):
            raise ValueError("NUL bytes are not allowed in args")
        return v


class PackageFile(BaseModel):
    path: str
    size: int
    sha256: str


class PackageRecord(BaseModel):
    """What the catalog stores for one immutable package version."""

    name: str
    version: str
    digest: str = Field(description="sha256 of the canonical artifact (content identity and integrity).")
    size: int = Field(description="Artifact size in bytes.")
    artifact_uri: str
    files: list[PackageFile]
    metadata: PackageMetadata
    lock_digest: str | None = Field(None, description="sha256 of the lock file, if any.")
    created_at: datetime
    created_by: str

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "digest": self.digest,
            "size": self.size,
            "artifact_uri": self.artifact_uri,
            "file_count": len(self.files),
            "entrypoint": self.metadata.entrypoint,
            "description": self.metadata.description,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }
