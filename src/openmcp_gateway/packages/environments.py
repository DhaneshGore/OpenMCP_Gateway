"""Isolated, cached Python environments for packages.

Each package runs under its own interpreter environment (a venv created from
the gateway's Python). Environments are keyed by an immutable digest of
everything that affects their contents (Python version, lock-file digest,
inheritance setting, index configuration), so they can be shared between
versions with identical dependencies and are never modified after creation.

Installation is deliberately restrictive: hash-pinned requirements only
(`--require-hashes`), wheels only (`--only-binary :all:`, so no package build
scripts run), no dependency resolution beyond the lock (`--no-deps`), the
operator's index settings only, a timeout, and the same Landlock sandbox as
servers (writes confined to the environment being built).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import sys
import sysconfig
import tempfile
from pathlib import Path

from openmcp_gateway.config import PackagesConfig
from openmcp_gateway.packages import bootstrap

logger = logging.getLogger("openmcp_gateway.packages")

BOOTSTRAP = Path(bootstrap.__file__).resolve()
_READY = ".openmcp-env.json"


class EnvironmentError_(Exception):
    pass


# Only the parts of /etc that programs commonly need: the dynamic loader,
# TLS trust stores, name resolution, users/groups and time zones. The rest of
# /etc (which may hold the gateway's own configuration) stays unreadable.
ETC_READ_PATHS = (
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/ssl",
    "/etc/ca-certificates",
    "/etc/pki",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/host.conf",
    "/etc/nsswitch.conf",
    "/etc/gai.conf",
    "/etc/services",
    "/etc/protocols",
    "/etc/passwd",
    "/etc/group",
    "/etc/localtime",
    "/etc/timezone",
    "/etc/mime.types",
    "/etc/os-release",
    # OS identification (read by e.g. pip's vendored `distro` module).
    "/etc/lsb-release",
    "/etc/debian_version",
    "/etc/redhat-release",
    "/etc/system-release",
    "/etc/alpine-release",
)


def system_read_paths() -> list[str]:
    """Read-only paths every sandboxed process needs."""
    paths = ["/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", *ETC_READ_PATHS, "/run/systemd/resolve"]
    paths += [sys.base_prefix, sys.base_exec_prefix]
    paths += ["/dev/null", "/dev/zero", "/dev/urandom", "/dev/random"]
    return sorted({p for p in paths if os.path.exists(p)})


def gateway_site_paths() -> list[str]:
    paths = sysconfig.get_paths()
    return sorted({paths["purelib"], paths["platlib"]})


def env_site_packages(env: Path) -> Path:
    v = sys.version_info
    return env / "lib" / f"python{v.major}.{v.minor}" / "site-packages"


def env_python(env: Path) -> Path:
    return env / "bin" / "python"


class EnvironmentManager:
    def __init__(self, config: PackagesConfig) -> None:
        self.config = config
        self.root = (config.cache_dir / "envs").resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._locks: dict[str, asyncio.Lock] = {}

    def key(self, lock_digest: str | None) -> str:
        deps = self.config.dependencies
        material = {
            "python": sys.version,
            "implementation": sys.implementation.cache_tag,
            "lock": lock_digest,
            "inherit": deps.inherit_gateway_packages,
            "gateway_site": gateway_site_paths() if deps.inherit_gateway_packages else [],
            "index": deps.index_url,
            "find_links": [str(p) for p in deps.find_links],
            "no_index": deps.no_index,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:32]

    async def ensure(self, lock_digest: str | None, lock_file: Path | None) -> Path:
        """Return the path of a ready environment, building it if necessary."""
        key = self.key(lock_digest)
        target = self.root / key
        async with self._locks.setdefault(key, asyncio.Lock()):
            if (target / _READY).is_file():
                return target
            if target.exists():  # leftover from a crashed build
                await asyncio.to_thread(shutil.rmtree, target, True)
            build = Path(tempfile.mkdtemp(dir=self.root, prefix=".build-"))
            try:
                await self._build(build, lock_file)
                (build / _READY).write_text(json.dumps({"key": key, "lock_digest": lock_digest}))
                try:
                    os.replace(build, target)
                except OSError:
                    # Another gateway sharing this cache finished the same build first.
                    if not (target / _READY).is_file():
                        raise
                    await asyncio.to_thread(shutil.rmtree, build, True)
            except BaseException:
                await asyncio.to_thread(shutil.rmtree, build, True)
                raise
            logger.info("package environment ready", extra={"env": key, "has_dependencies": bool(lock_file)})
            return target

    async def _build(self, env: Path, lock_file: Path | None) -> None:
        await self._run([sys.executable, "-m", "venv", "--without-pip", str(env)], timeout=120)
        site = env_site_packages(env)
        if self.config.dependencies.inherit_gateway_packages:
            # Processed by `site` even in isolated mode; appended after the
            # environment's own packages, so locked dependencies win.
            (site / "_openmcp_gateway_packages.pth").write_text("\n".join(gateway_site_paths()) + "\n")
        if lock_file is None:
            return
        import importlib.util

        if importlib.util.find_spec("pip") is None:
            raise EnvironmentError_("dependency installation needs pip in the gateway's environment")
        deps = self.config.dependencies
        argv = [
            "--python", str(env_python(env)),
            "install",
            "--require-hashes",
            "--only-binary", ":all:",
            "--no-deps",
            "--no-input",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-warn-script-location",
            "-r", str(lock_file),
        ]  # fmt: skip
        if deps.index_url:
            argv += ["--index-url", deps.index_url]
        for link in deps.find_links:
            argv += ["--find-links", str(link.resolve())]
        if deps.no_index:
            argv.append("--no-index")
        tmp = env / ".install-tmp"
        tmp.mkdir()
        spec = {
            "landlock": self.config.sandbox.isolation == "landlock",
            "root": str(env),
            "module": "pip",
            "argv": argv,
            "read_only": [
                *system_read_paths(),
                sys.prefix,
                *gateway_site_paths(),
                str(lock_file.parent),
                *[str(p.resolve()) for p in deps.find_links],
            ],
            "read_write": [str(env), "/dev/null"],
            "deny_network": False,
            "seccomp": self.config.sandbox.block_unix_sockets,
            "rlimits": {"RLIMIT_NOFILE": 1024},
        }
        env_vars = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp), "TMPDIR": str(tmp)}
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "PIP_CERT"):
            if name in os.environ:
                env_vars[name] = os.environ[name]
        try:
            await self._run(
                [sys.executable, "-I", "-B", str(BOOTSTRAP), json.dumps(spec)],
                timeout=deps.install_timeout,
                env=env_vars,
                what="dependency installation",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    async def _run(
        argv: list[str], *, timeout: float, env: dict[str, str] | None = None, what: str = ""
    ) -> None:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, 9)
            await proc.wait()
            raise EnvironmentError_(f"{what or argv[0]} timed out after {timeout:g}s") from None
        if proc.returncode != 0:
            tail = out.decode(errors="replace").strip().splitlines()[-5:]
            raise EnvironmentError_(f"{what or 'environment setup'} failed: " + " | ".join(tail)[:1000])
