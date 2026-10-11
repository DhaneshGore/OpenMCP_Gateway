"""Running a registered package version as an MCP server.

Per start: look up the exact version, fetch the artifact and verify its
checksum, ensure the (cached, digest-keyed) environment, extract the package
into a fresh private instance directory, verify every file against the
manifest, and launch the entrypoint through the sandboxed bootstrap with an
argument vector. The instance directory is removed on stop and after a crash.

Layout of one instance (mode 0700, unique per start)::

    <runtime_dir>/<server-id>/<random>/
        pkg/    read-only copy of the package (cwd, first on sys.path)
        data/   writable; $HOME and $OPENMCP_PACKAGE_DATA
        tmp/    writable; $TMPDIR
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import mcp_types as types

from openmcp_gateway.config import PackagesConfig, PackageServerConfig, SandboxConfig
from openmcp_gateway.errors import UpstreamStartError
from openmcp_gateway.packages.archive import IntegrityError, extract_verified
from openmcp_gateway.packages.bootstrap import landlock_abi
from openmcp_gateway.packages.environments import (
    BOOTSTRAP,
    EnvironmentError_,
    EnvironmentManager,
    env_python,
    gateway_site_paths,
    system_read_paths,
)
from openmcp_gateway.packages.metadata import PackageRecord
from openmcp_gateway.packages.service import PackageError, PackageService, make_writable
from openmcp_gateway.redaction import redactor
from openmcp_gateway.security.env import build_child_env
from openmcp_gateway.upstream.base import StderrLine, UpstreamConnection
from openmcp_gateway.upstream.jsonrpc import NotificationHandler
from openmcp_gateway.upstream.stdio import LaunchSpec, StdioUpstream

logger = logging.getLogger("openmcp_gateway.packages")


OWNER_FILE = ".owner-pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class PackageRuntime:
    """Shared services for package servers (one per gateway)."""

    def __init__(self, config: PackagesConfig, service: PackageService) -> None:
        self.config = config
        self.service = service
        self.environments = EnvironmentManager(config)
        self.runtime_root = config.runtime_dir.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runtime_root, 0o700)
        self._abi: int | None = None
        self.sweep_stale_instances()

    def sweep_stale_instances(self) -> int:
        """Remove instance directories left behind by a gateway that died without cleanup.

        Only directories whose recorded owner process no longer exists are removed,
        so a live gateway's instances are never touched.
        """
        removed = 0
        for instance in self.runtime_root.glob("*/*"):
            owner = instance / OWNER_FILE
            try:
                pid = int(owner.read_text().strip())
            except (OSError, ValueError):
                continue  # not ours, or still being created
            if pid == os.getpid() or _pid_alive(pid):
                continue
            make_writable(instance)
            shutil.rmtree(instance, ignore_errors=True)
            with contextlib.suppress(OSError):
                instance.parent.rmdir()
            removed += 1
        if removed:
            logger.info("removed stale package runtime directories", extra={"count": removed})
        return removed

    def isolation_abi(self) -> int:
        if self._abi is None:
            self._abi = landlock_abi()
        return self._abi

    def check_isolation(self, network: str) -> None:
        if self.config.sandbox.isolation == "none":
            if network == "deny":
                raise UpstreamStartError("network: deny requires packages.sandbox.isolation: landlock")
            return
        abi = self.isolation_abi()
        if abi < 1:
            raise UpstreamStartError(
                "package isolation requires Linux Landlock, which is unavailable here; "
                "set packages.sandbox.isolation: none only on a trusted development machine"
            )
        if network == "deny" and abi < 4:
            raise UpstreamStartError("network: deny requires Landlock ABI 4 (Linux 6.7 or newer)")


class PackageUpstream:
    """UpstreamConnection for a package server; wraps a StdioUpstream."""

    def __init__(
        self,
        config: PackageServerConfig,
        runtime: PackageRuntime,
        *,
        max_message_bytes: int,
        max_pending: int,
        stderr_lines: int,
        stderr_line_bytes: int,
        on_notification: NotificationHandler | None = None,
        on_exit: Callable[[UpstreamConnection], None] | None = None,
        host_env: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.server_id = config.id
        self._runtime = runtime
        self._stdio_opts: dict[str, Any] = {
            "max_message_bytes": max_message_bytes,
            "max_pending": max_pending,
            "stderr_lines": stderr_lines,
            "stderr_line_bytes": stderr_line_bytes,
            "on_notification": on_notification,
        }
        self._on_exit = on_exit
        self._host_env = host_env
        self._inner: StdioUpstream | None = None
        self.instance_dir: Path | None = None
        self.record: PackageRecord | None = None
        self.environment: Path | None = None
        self._cleanup_task: asyncio.Task[None] | None = None

    # --------------------------------------------------------- delegation

    @property
    def protocol_version(self) -> str | None:
        return self._inner.protocol_version if self._inner else None

    @property
    def server_info(self) -> types.Implementation | None:
        return self._inner.server_info if self._inner else None

    @property
    def capabilities(self) -> types.ServerCapabilities | None:
        return self._inner.capabilities if self._inner else None

    @property
    def instructions(self) -> str | None:
        return self._inner.instructions if self._inner else None

    @property
    def started_at(self) -> float | None:
        return self._inner.started_at if self._inner else None

    @property
    def alive(self) -> bool:
        return self._inner is not None and self._inner.alive

    @property
    def pid(self) -> int | None:
        return self._inner.pid if self._inner else None

    @property
    def returncode(self) -> int | None:
        return self._inner.returncode if self._inner else None

    @property
    def stderr(self) -> Sequence[StderrLine]:
        return self._inner.stderr if self._inner else ()

    def stats(self) -> dict[str, int]:
        return self._inner.stats() if self._inner else {}

    async def request(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        if self._inner is None:
            from openmcp_gateway.errors import UpstreamClosedError

            raise UpstreamClosedError(f"server {self.server_id!r} is not running")
        return await self._inner.request(method, params, timeout=timeout)

    # --------------------------------------------------------- lifecycle

    async def start(self) -> None:
        cfg = self.config
        label = f"server {cfg.id!r}"
        self._runtime.check_isolation(cfg.network)
        try:
            async with asyncio.timeout(
                cfg.timeouts.startup + self._runtime.config.dependencies.install_timeout
            ):
                launch = await self._prepare(label)
        except TimeoutError:
            await self._cleanup()
            raise UpstreamStartError(f"{label}: preparing the package timed out") from None
        except BaseException:
            await self._cleanup()
            raise
        inner = StdioUpstream(cfg, **self._stdio_opts, on_exit=self._inner_exited, launch=launch)
        self._inner = inner
        try:
            await inner.start()
        except BaseException:
            self._inner = None
            await self._cleanup()
            raise

    async def _prepare(self, label: str) -> LaunchSpec:
        cfg = self.config
        service = self._runtime.service
        try:
            record = await service.get(cfg.package, cfg.version)
            artifact = await service.fetch_verified(record)
        except PackageError as exc:
            raise UpstreamStartError(f"{label}: {exc.message}") from None
        self.record = record

        instance = self._runtime.runtime_root / cfg.id / secrets.token_hex(8)
        instance.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        instance.mkdir(mode=0o700)
        (instance / OWNER_FILE).write_text(str(os.getpid()))
        self.instance_dir = instance
        pkg, data, tmp = instance / "pkg", instance / "data", instance / "tmp"
        try:
            await asyncio.to_thread(
                extract_verified, artifact, record.digest, record.files, pkg, service.policy
            )
        except IntegrityError as exc:
            raise UpstreamStartError(f"{label}: integrity check failed: {exc}") from None
        data.mkdir(mode=0o700)
        tmp.mkdir(mode=0o700)

        lock_rel = record.metadata.lock or ("requirements.lock" if record.lock_digest else None)
        lock_file = pkg / lock_rel if (record.lock_digest and lock_rel) else None
        try:
            env = await self._runtime.environments.ensure(record.lock_digest, lock_file)
        except EnvironmentError_ as exc:
            raise UpstreamStartError(f"{label}: {exc}") from None
        self.environment = env

        child_env, child_secrets = build_child_env(cfg, self._host_env)
        redactor.register(child_secrets)
        child_env.update(
            {
                "HOME": str(data),
                "TMPDIR": str(tmp),
                "OPENMCP_PACKAGE_ROOT": str(pkg),
                "OPENMCP_PACKAGE_DATA": str(data),
                "OPENMCP_PACKAGE_NAME": record.name,
                "OPENMCP_PACKAGE_VERSION": record.version,
            }
        )
        sandbox = self._runtime.config.sandbox
        read_only = [
            *system_read_paths(),
            str(env),
            str(pkg),
            *[str(p.resolve()) for p in sandbox.extra_read_paths],
        ]
        if self._runtime.config.dependencies.inherit_gateway_packages:
            read_only += [sys.prefix, *gateway_site_paths()]
        spec = {
            "landlock": sandbox.isolation == "landlock",
            "root": str(pkg),
            "entry": record.metadata.entrypoint,
            "argv": [*record.metadata.args, *cfg.args],
            "read_only": sorted(set(read_only)),
            "read_write": [str(data), str(tmp), "/dev/null"],
            "deny_network": cfg.network == "deny",
            "seccomp": sandbox.block_unix_sockets,
            "rlimits": _rlimits(sandbox),
        }
        return LaunchSpec(
            executable=str(env_python(env)),
            args=["-I", "-B", str(BOOTSTRAP), json.dumps(spec)],
            cwd=pkg,
            env=child_env,
        )

    async def stop(self) -> None:
        inner = self._inner
        if inner is not None:
            await inner.stop()
        await self._cleanup()

    def _inner_exited(self, _inner: StdioUpstream) -> None:
        # The process is gone; remove its private files, then tell the manager.
        self._cleanup_task = asyncio.ensure_future(self._cleanup())
        if self._on_exit is not None:
            self._on_exit(self)

    async def _cleanup(self) -> None:
        instance = self.instance_dir
        if instance is None:
            return
        self.instance_dir = None

        def remove() -> None:
            make_writable(instance)
            shutil.rmtree(instance, ignore_errors=True)
            with contextlib.suppress(OSError):
                instance.parent.rmdir()  # only succeeds when no other instance remains

        await asyncio.to_thread(remove)


def _rlimits(sandbox: SandboxConfig) -> dict[str, int]:
    limits = {"RLIMIT_NOFILE": sandbox.max_open_files, "RLIMIT_FSIZE": sandbox.max_file_size_bytes}
    if sandbox.max_memory_bytes is not None:
        limits["RLIMIT_AS"] = sandbox.max_memory_bytes
    if sandbox.max_cpu_seconds is not None:
        limits["RLIMIT_CPU"] = sandbox.max_cpu_seconds
    if sandbox.max_processes is not None:
        limits["RLIMIT_NPROC"] = sandbox.max_processes
    return limits


def describe(upstream: object) -> dict[str, Any] | None:
    """Package details for the admin API, if `upstream` is a package server."""
    if not isinstance(upstream, PackageUpstream) or upstream.record is None:
        return None
    record = upstream.record
    return {
        "package": record.name,
        "version": record.version,
        "digest": record.digest,
        "environment": upstream.environment.name if upstream.environment else None,
        "instance_dir": str(upstream.instance_dir) if upstream.instance_dir else None,
    }
