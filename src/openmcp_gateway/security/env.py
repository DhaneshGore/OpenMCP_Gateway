"""Construction of upstream subprocess environments.

The gateway's own environment (which may hold cloud credentials, tokens, etc.)
is **not** inherited. A child receives only:

1. a small allowlist of variables needed to run programs at all,
2. variables listed in the server's `inherit_env`,
3. literal values from `env`,
4. secrets copied by reference from `env_from_host` (registered for redaction).
"""

from __future__ import annotations

import os

from openmcp_gateway.config import PackageServerConfig, StdioServerConfig
from openmcp_gateway.errors import UpstreamStartError

BASE_ALLOWLIST_POSIX = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ")
BASE_ALLOWLIST_WINDOWS = (
    "APPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERPROFILE",
)


def base_allowlist() -> tuple[str, ...]:
    return BASE_ALLOWLIST_WINDOWS if os.name == "nt" else BASE_ALLOWLIST_POSIX


def build_child_env(
    config: StdioServerConfig | PackageServerConfig, host_env: dict[str, str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Return (environment, secret_values) for a server's subprocess."""
    source = dict(os.environ) if host_env is None else host_env
    env: dict[str, str] = {}
    for name in (*base_allowlist(), *config.inherit_env):
        if name in source:
            env[name] = source[name]
    env.update(config.env)

    secrets: list[str] = []
    missing: list[str] = []
    for child_name, host_name in config.env_from_host.items():
        value = source.get(host_name)
        if value is None:
            missing.append(host_name)
            continue
        env[child_name] = value
        secrets.append(value)
    if missing:
        # Names only — never values.
        raise UpstreamStartError(
            f"server {config.id!r}: required gateway environment variable(s) not set: {', '.join(missing)}"
        )
    return env, secrets
