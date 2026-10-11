"""Sandboxed launcher for package processes. Standard library only.

Executed as a *script* by the package environment's interpreter in isolated
mode (``python -I -B bootstrap.py SPEC_JSON``), so neither environment
variables nor the current directory influence imports. It:

1. applies resource limits,
2. confines the process with Landlock (Linux): read-only access to the system,
   the interpreter and the package; read-write access only to the instance's
   own data and temp directories; optionally no TCP at all,
3. sets no_new_privs and installs a small seccomp filter that denies creating
   AF_UNIX sockets (Landlock before ABI 6 does not control connecting to
   socket files such as the Docker socket; ``socketpair`` stays allowed, so
   asyncio works), denies path-based ``truncate`` when Landlock cannot
   control it (ABI < 3), and kills syscalls made through a foreign ABI,
4. puts the package root first on ``sys.path`` and runs the entrypoint (or a
   module, for dependency installation) as ``__main__``.

Landlock domains are inherited by children and cannot be lifted, and a
sandboxed process cannot ptrace or read /proc of processes outside its own
domain, so sibling servers cannot see each other's files or environment.
"""

from __future__ import annotations

import ctypes
import json
import os
import runpy
import sys

EXIT_SANDBOX_FAILED = 70

# Landlock UAPI (linux/landlock.h). Syscall numbers are identical on all
# architectures because the calls were added after syscall-table unification.
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
FS_IOCTL_DEV = 1 << 15  # ABI 5
NET_BIND_TCP = 1 << 0  # ABI 4
NET_CONNECT_TCP = 1 << 1  # ABI 4
SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0  # ABI 6
SCOPE_SIGNAL = 1 << 1  # ABI 6

_FILE_ONLY = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class _PathBeneathAttr(ctypes.Structure):
    # The kernel struct is packed (12 bytes). Without packing the two fields
    # keep the same offsets and the kernel copies only the first 12 bytes.
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def landlock_abi() -> int:
    """Return the kernel's Landlock ABI version, or 0 if unavailable."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        libc = _libc()
        libc.syscall.restype = ctypes.c_long
        abi = libc.syscall(
            _SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_RULESET_VERSION)
        )
    except (OSError, AttributeError):
        return 0
    return int(abi) if abi > 0 else 0


def _fs_mask(abi: int) -> int:
    mask = (1 << 13) - 1
    if abi >= 2:
        mask |= FS_REFER
    if abi >= 3:
        mask |= FS_TRUNCATE
    if abi >= 5:
        mask |= FS_IOCTL_DEV
    return mask


def apply_landlock(read_only: list[str], read_write: list[str], deny_network: bool) -> int:
    """Confine this process. Returns the ABI used. Raises OSError on failure."""
    abi = landlock_abi()
    if abi < 1:
        raise OSError("Landlock is not available on this kernel")
    libc = _libc()
    libc.syscall.restype = ctypes.c_long
    handled = _fs_mask(abi)
    attr = _RulesetAttr(handled_access_fs=handled, handled_access_net=0, scoped=0)
    size = 8
    if abi >= 4:
        size = 16
        if deny_network:
            attr.handled_access_net = NET_BIND_TCP | NET_CONNECT_TCP
    elif deny_network:
        raise OSError("network isolation needs Landlock ABI 4 (Linux 6.7+)")
    if abi >= 6:
        size = 24
        attr.scoped = SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL
    ruleset_fd = libc.syscall(
        _SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0)
    )
    if ruleset_fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")

    read_access = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
    try:
        for paths, access in ((read_only, read_access), (read_write, handled)):
            for path in paths:
                try:
                    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                except FileNotFoundError:
                    continue
                try:
                    allowed = access & handled
                    if not os.path.isdir(path):
                        allowed &= _FILE_ONLY
                    rule = _PathBeneathAttr(allowed_access=allowed, parent_fd=fd)
                    if (
                        libc.syscall(_SYS_ADD_RULE, ruleset_fd, _RULE_PATH_BENEATH, ctypes.byref(rule), 0)
                        != 0
                    ):
                        raise OSError(ctypes.get_errno(), f"landlock_add_rule failed for {path}")
                finally:
                    os.close(fd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")
        if libc.syscall(_SYS_RESTRICT_SELF, ruleset_fd, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(ruleset_fd)
    return abi


# ------------------------------------------------------------------ seccomp

_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_RET_ALLOW = 0x7FFF0000
_RET_KILL_PROCESS = 0x80000000
_RET_ERRNO_EACCES = 0x00050000 | 13
_LD_W_ABS = 0x20  # BPF_LD | BPF_W | BPF_ABS
_JEQ_K = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
_JGE_K = 0x35  # BPF_JMP | BPF_JGE | BPF_K
_RET_K = 0x06  # BPF_RET | BPF_K
_AF_UNIX = 1
_X32_BIT = 0x40000000

# machine -> (AUDIT_ARCH_*, __NR_socket, __NR_truncate)
SECCOMP_ARCHES = {
    "x86_64": (0xC000003E, 41, 76),
    "aarch64": (0xC00000B7, 198, 45),
}


class _SockFilter(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    )


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter)))


def seccomp_program(machine: str, block_truncate: bool) -> list[tuple[int, int, int, int]]:
    """Return the BPF program as (code, jt, jf, k) tuples. Raises OSError for unknown machines."""
    if machine not in SECCOMP_ARCHES:
        raise OSError(f"socket filtering is not supported on architecture {machine!r}")
    arch, nr_socket, nr_truncate = SECCOMP_ARCHES[machine]
    prog: list[tuple[int, int, int, int]] = [
        (_LD_W_ABS, 0, 0, 4),  # seccomp_data.arch
        (_JEQ_K, 1, 0, arch),
        (_RET_K, 0, 0, _RET_KILL_PROCESS),  # foreign ABI (e.g. i386 compat calls)
        (_LD_W_ABS, 0, 0, 0),  # seccomp_data.nr
    ]
    if machine == "x86_64":
        prog += [(_JGE_K, 0, 1, _X32_BIT), (_RET_K, 0, 0, _RET_KILL_PROCESS)]  # x32 ABI
    if block_truncate:
        prog += [(_JEQ_K, 0, 1, nr_truncate), (_RET_K, 0, 0, _RET_ERRNO_EACCES)]
    prog += [
        (_JEQ_K, 0, 3, nr_socket),
        (_LD_W_ABS, 0, 0, 16),  # args[0] (domain), low 32 bits; both arches are little-endian
        (_JEQ_K, 0, 1, _AF_UNIX),
        (_RET_K, 0, 0, _RET_ERRNO_EACCES),
        (_RET_K, 0, 0, _RET_ALLOW),
    ]
    return prog


def apply_seccomp(block_truncate: bool) -> None:
    import platform

    prog = seccomp_program(platform.machine(), block_truncate)
    filters = (_SockFilter * len(prog))(*(_SockFilter(*ins) for ins in prog))
    fprog = _SockFprog(len=len(prog), filter=filters)
    libc = _libc()
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "installing the seccomp filter failed")


def _apply_rlimits(limits: dict[str, int]) -> None:
    import resource

    for name, value in limits.items():
        res = getattr(resource, name)
        _soft, hard = resource.getrlimit(res)
        new_hard = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(res, (min(value, new_hard), new_hard))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: bootstrap.py SPEC_JSON", file=sys.stderr)
        return 2
    spec = json.loads(argv[1])
    try:
        _apply_rlimits(spec.get("rlimits", {}))
        if spec.get("landlock"):
            abi = apply_landlock(spec["read_only"], spec["read_write"], spec.get("deny_network", False))
            if spec.get("seccomp", True):
                apply_seccomp(block_truncate=abi < 3)
        elif sys.platform.startswith("linux"):
            _libc().prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    except OSError as exc:
        print(f"openmcp sandbox: {exc}", file=sys.stderr)
        return EXIT_SANDBOX_FAILED

    root = spec["root"]
    os.chdir(root)
    sys.path.insert(0, root)
    if "module" in spec:
        sys.argv = [spec["module"], *spec.get("argv", [])]
        runpy.run_module(spec["module"], run_name="__main__", alter_sys=True)
        return 0
    entry = os.path.join(root, spec["entry"])
    sys.argv = [entry, *spec.get("argv", [])]
    runpy.run_path(entry, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
