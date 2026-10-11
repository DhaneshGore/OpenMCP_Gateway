"""Immutable artifact storage (the artifact-store extension point).

Keys embed the content digest, and writes refuse to overwrite: an artifact,
once stored, never changes. Two backends: a local directory (development and
single-host deployments) and any S3-compatible object store.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol


class ArtifactExistsError(Exception):
    pass


class ArtifactNotFoundError(Exception):
    pass


class ArtifactStore(Protocol):
    def uri(self, key: str) -> str: ...

    async def put(self, key: str, data: bytes) -> str:
        """Store `data` under `key` if absent; return its URI. Raises ArtifactExistsError."""
        ...

    async def download(self, key: str, dest: Path) -> None:
        """Write the artifact to `dest` (overwriting). Raises ArtifactNotFoundError."""
        ...

    async def delete(self, key: str) -> None: ...

    async def close(self) -> None: ...


def artifact_key(name: str, version: str, digest: str) -> str:
    return f"{name}/{version}/{digest}.zip"


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError(f"invalid artifact key {key!r}")
        return path

    def uri(self, key: str) -> str:
        return self._path(key).as_uri()

    async def put(self, key: str, data: bytes) -> str:
        return await asyncio.to_thread(self._put, key, data)

    def _put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o444)
            try:
                os.link(tmp, path)  # atomic and exclusive: fails if the key exists
            except FileExistsError:
                raise ArtifactExistsError(key) from None
        finally:
            os.unlink(tmp)
        return path.as_uri()

    async def download(self, key: str, dest: Path) -> None:
        def run() -> None:
            src = self._path(key)
            if not src.is_file():
                raise ArtifactNotFoundError(key)
            with src.open("rb") as fin, dest.open("wb") as fout:
                while chunk := fin.read(1024 * 1024):
                    fout.write(chunk)

        await asyncio.to_thread(run)

    async def delete(self, key: str) -> None:
        def run() -> None:
            self._path(key).unlink(missing_ok=True)

        await asyncio.to_thread(run)

    async def close(self) -> None:
        return None


class S3ArtifactStore:
    """S3-compatible storage via boto3 (install the `s3` extra).

    Uses conditional writes (`If-None-Match: *`) so concurrent registrations
    cannot overwrite each other; a pre-check also covers stores that ignore
    the header.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "S3 storage requires the 's3' extra: pip install 'openmcp-gateway[s3]'"
            ) from None
        self.bucket = bucket
        self.prefix = prefix
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60
            ),
        )

    def _key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._key(key)}"

    async def put(self, key: str, data: bytes) -> str:
        def run() -> str:
            from botocore.exceptions import ClientError

            full = self._key(key)
            try:
                self._client.head_object(Bucket=self.bucket, Key=full)
                raise ArtifactExistsError(key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                    raise
            try:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=full,
                    Body=data,
                    ContentType="application/zip",
                    IfNoneMatch="*",
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
                    raise ArtifactExistsError(key) from None
                raise
            return self.uri(key)

        return await asyncio.to_thread(run)

    async def download(self, key: str, dest: Path) -> None:
        def run() -> None:
            from botocore.exceptions import ClientError

            try:
                self._client.download_file(self.bucket, self._key(key), str(dest))
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    raise ArtifactNotFoundError(key) from None
                raise

        await asyncio.to_thread(run)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=self._key(key))

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)
