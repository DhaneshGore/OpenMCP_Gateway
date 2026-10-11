"""The package sandbox (Landlock + seccomp + rlimits), exercised directly.

Each probe runs under the real bootstrap with the same kind of spec a package
server gets, and reports what was allowed. These are the regression tests for
the escape routes found in the release review.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from openmcp_gateway.packages.bootstrap import SECCOMP_ARCHES, seccomp_program
from openmcp_gateway.packages.environments import BOOTSTRAP, gateway_site_paths, system_read_paths
from tests.conftest import require_landlock

pytestmark = pytest.mark.e2e

PROBE = r"""
import json, os, socket, ssl, subprocess, sys, resource, asyncio
args = json.loads(sys.argv[1])
out = {}
def t(name, fn):
    try:
        fn(); out[name] = "allowed"
    except Exception as exc:
        out[name] = type(exc).__name__
t("read_other", lambda: open(args["other"]).read())
t("child_read_other", lambda: subprocess.run(["cat", args["other"]], check=True, capture_output=True))
t("write_own_data", lambda: open(os.path.join(args["data"], "x"), "w").write("x"))
t("write_tmp", lambda: open("/tmp/openmcp-sandbox-probe", "w").write("x"))
t("unix_socket", lambda: socket.socket(socket.AF_UNIX))
t("child_unix_socket", lambda: subprocess.run(
    [sys.executable, "-c", "import socket; socket.socket(socket.AF_UNIX)"], check=True, capture_output=True))
t("socketpair", lambda: socket.socketpair())
t("connect_listener", lambda: socket.socket(socket.AF_UNIX).connect(args["listener"]))
def docker_socket():
    if not os.path.exists("/var/run/docker.sock"):
        raise PermissionError("no Docker socket on this host")
    socket.socket(socket.AF_UNIX).connect("/var/run/docker.sock")
t("docker_socket", docker_socket)
t("asyncio", lambda: asyncio.run(asyncio.sleep(0)))
t("tls_trust_store", lambda: ssl.create_default_context())
t("etc_passwd", lambda: open("/etc/passwd").read())
t("etc_listing", lambda: os.listdir("/etc"))
t("proc_self", lambda: open("/proc/self/status").read())
def etc_extra():
    if not args["etc_extra"]:
        raise PermissionError("no unlisted /etc file to probe")
    open(args["etc_extra"]).read()
t("etc_extra", etc_extra)
out["rlimit_as"] = resource.getrlimit(resource.RLIMIT_AS)[0]
out["rlimit_nproc"] = resource.getrlimit(resource.RLIMIT_NPROC)[0]
print(json.dumps(out))
"""


def run_probe(
    tmp_path: Path, *, seccomp: bool = True, rlimits: dict[str, int] | None = None
) -> dict[str, Any]:
    pkg, data, tmp, other = (tmp_path / n for n in ("pkg", "data", "tmp", "other"))
    for d in (pkg, data, tmp, other):
        d.mkdir(exist_ok=True)
    (pkg / "probe.py").write_text(PROBE)
    (other / "secret.txt").write_text("secret")
    etc_extra = next(
        (str(p) for p in sorted(Path("/etc").iterdir()) if p.is_file() and str(p) not in system_read_paths()
         and os.access(p, os.R_OK)),
        "",
    )  # fmt: skip
    # A listening UNIX socket outside the sandbox, standing in for the Docker daemon.
    listener_path = other / "d.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(listener_path))
    listener.listen(1)
    probe_args = {
        "other": str(other / "secret.txt"),
        "data": str(data),
        "etc_extra": etc_extra,
        "listener": str(listener_path),
    }
    spec = {
        "landlock": True,
        "seccomp": seccomp,
        "root": str(pkg),
        "entry": "probe.py",
        "argv": [json.dumps(probe_args)],
        "read_only": sorted({*system_read_paths(), sys.prefix, *gateway_site_paths(), str(pkg)}),
        "read_write": [str(data), str(tmp), "/dev/null"],
        "deny_network": False,
        "rlimits": rlimits or {"RLIMIT_NOFILE": 256},
    }
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(BOOTSTRAP), json.dumps(spec)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            timeout=60,
            check=False,
        )
    finally:
        listener.close()
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_filesystem_and_socket_restrictions(tmp_path: Path) -> None:
    require_landlock()
    r = run_probe(tmp_path)
    # Other directories, /tmp, /proc and unlisted /etc files are out of reach...
    assert r["read_other"] == "PermissionError"
    assert r["child_read_other"] == "CalledProcessError"  # restrictions are inherited
    assert r["write_tmp"] == "PermissionError"
    assert r["proc_self"] == "PermissionError"
    assert r["etc_listing"] == "PermissionError"
    assert r["etc_extra"] == "PermissionError"
    # ...UNIX sockets cannot be created (Docker socket, D-Bus, abstract sockets)...
    assert r["unix_socket"] == "PermissionError"
    assert r["child_unix_socket"] == "CalledProcessError"
    assert r["connect_listener"] == "PermissionError"
    assert r["docker_socket"] == "PermissionError"
    # ...while normal Python operation keeps working.
    assert r["write_own_data"] == "allowed"
    assert r["socketpair"] == "allowed"
    assert r["asyncio"] == "allowed"
    assert r["tls_trust_store"] == "allowed"
    assert r["etc_passwd"] == "allowed"


def test_unix_socket_filter_can_be_disabled(tmp_path: Path) -> None:
    require_landlock()
    r = run_probe(tmp_path, seccomp=False)
    assert r["unix_socket"] == "allowed"
    # Landlock alone does not stop connecting to a listening socket; this is the
    # gap the seccomp filter closes (and proves the probe above can detect it).
    assert r["connect_listener"] == "allowed"


def test_resource_limits_are_applied(tmp_path: Path) -> None:
    require_landlock()
    r = run_probe(tmp_path, rlimits={"RLIMIT_NOFILE": 256, "RLIMIT_AS": 2 << 30, "RLIMIT_NPROC": 4096})
    assert r["rlimit_as"] == 2 << 30
    assert r["rlimit_nproc"] == 4096


@pytest.mark.parametrize("machine", sorted(SECCOMP_ARCHES))
@pytest.mark.parametrize("block_truncate", [True, False])
def test_seccomp_program_shape(machine: str, block_truncate: bool) -> None:
    arch, nr_socket, nr_truncate = SECCOMP_ARCHES[machine]
    prog = seccomp_program(machine, block_truncate)
    ks = [ins[3] for ins in prog]
    assert prog[0] == (0x20, 0, 0, 4) and prog[1][3] == arch
    assert prog[2] == (0x06, 0, 0, 0x80000000)  # foreign ABI is killed
    assert nr_socket in ks
    assert (nr_truncate in ks) == block_truncate
    assert prog[-1] == (0x06, 0, 0, 0x7FFF0000)
    # Every jump stays inside the program.
    for i, (code, jt, jf, _k) in enumerate(prog):
        if code in (0x15, 0x35):
            assert i + 1 + max(jt, jf) < len(prog)


def test_unknown_architecture_fails_closed() -> None:
    with pytest.raises(OSError, match="not supported"):
        seccomp_program("riscv64", False)


def test_stale_instance_sweep(tmp_path: Path) -> None:
    from openmcp_gateway.config import PackagesConfig
    from openmcp_gateway.packages.runtime import OWNER_FILE, PackageRuntime

    runtime_dir = tmp_path / "runtime"
    dead = runtime_dir / "srv" / "dead"
    live = runtime_dir / "srv" / "live"
    foreign = runtime_dir / "srv" / "no-owner"
    for d in (dead, live, foreign):
        (d / "pkg").mkdir(parents=True)
        os.chmod(d / "pkg", 0o555)
    dead_pid = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid.wait()
    (dead / OWNER_FILE).write_text(str(dead_pid.pid))
    (live / OWNER_FILE).write_text(str(os.getppid()))  # a process that is certainly alive
    cfg = PackagesConfig(
        enabled=True,
        runtime_dir=runtime_dir,
        cache_dir=tmp_path / "cache",
        artifacts={"path": str(tmp_path / "a")},
        catalog={"path": str(tmp_path / "c.db")},
    )
    PackageRuntime(cfg, service=None)  # type: ignore[arg-type]
    assert not dead.exists()
    assert live.exists() and foreign.exists()
