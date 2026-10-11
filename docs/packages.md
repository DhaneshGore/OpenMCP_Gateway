# MCP server packages

A **package** is a complete, multi-file MCP server: an entrypoint plus any
number of modules, nested packages, schemas, templates and configuration
files. The gateway stores each package version as an immutable, checksummed
artifact and runs it in a private, sandboxed runtime directory.

Packages are **disabled by default** (`packages.enabled: false`) because
running a package means running uploaded code. Everything on this page is
implemented and covered by tests unless marked otherwise.

## Package format

```text
weather-mcp/
    metadata.json          required
    server.py              the entrypoint (configurable)
    requirements.txt       optional, human-readable
    requirements.lock      required if there are dependencies (hash-pinned)
    README.md
    tools/__init__.py
    tools/weather.py
    utils/api_client.py
    schemas/weather.json
    config/defaults.yaml
    templates/report.txt
```

A ZIP may contain the files at its root or inside a single top-level folder
(`weather-mcp/…`); the folder is stripped. The whole relative directory
structure is preserved, not just `server.py`.

### `metadata.json`

```json
{
  "name": "weather-mcp",
  "version": "1.0.0",
  "description": "What the server does",
  "entrypoint": "server.py",
  "args": [],
  "requirements": "requirements.txt",
  "lock": "requirements.lock",
  "license": "Apache-2.0",
  "authors": ["you"],
  "homepage": "https://example.com"
}
```

| Field | Rules |
| --- | --- |
| `name` | `^[a-z0-9][a-z0-9-]{0,63}$` |
| `version` | semantic version (`1.2.3`, `1.2.3-rc.1`) |
| `entrypoint` | relative path to a `.py` file in the package (default `server.py`) |
| `args` | default arguments for the entrypoint |
| `requirements`, `lock` | relative paths; default to `requirements.txt` / `requirements.lock` if present |

Unknown fields are rejected.

### What gets rejected

Validation runs on upload and again before every start. It never executes
package code: Python files are compiled to detect syntax errors, nothing more.

| Check | Default limit / rule (`packages.limits`, `packages.allowed_*`) |
| --- | --- |
| Archive size | 50 MiB |
| File count | 2,000 |
| Total extracted size | 200 MiB; any single file 50 MiB |
| Compression ratio | 100:1 per file and overall (files over 64 KiB) |
| Path depth / length | 16 levels / 255 bytes |
| Paths | no absolute paths, drive letters, `..`, `.`, empty components, backslashes or control characters |
| Duplicates | exact, Unicode-normalised and case-insensitive collisions |
| Entry types | regular files and directories only: no symlinks, devices, FIFOs; no encrypted entries |
| File types | an allowlist of non-executable extensions (`.py`, `.json`, `.yaml`, `.txt`, `.md`, templates, …); native code (`.so`, `.pyd`, `.dll`), bytecode, shell scripts and `.pth` files can never be allowed |
| Header lies | each entry is decompressed with a hard limit and CRC check; declared sizes are not trusted |
| Metadata | must exist and match the schema; the entrypoint must exist and be a `.py` file |
| Python | every `.py` file must compile |
| Dependencies | see [Dependencies](#dependencies) |

`openmcp package validate PATH` runs the same checks locally without contacting a gateway.

## Versions, digests and integrity

* An accepted package is re-packed into a **canonical archive**: sorted
  entries, fixed timestamps, no compression. Its SHA-256 (the *digest*)
  therefore identifies the content, whichever tool created the upload.
* `(name, version)` is unique and immutable. Uploading identical content again
  returns the existing version (HTTP 200). Uploading *different* content
  under an existing version is rejected (HTTP 409): publish a new version.
* The catalog records, per version: metadata, digest, size, artifact URI, the
  path/size/SHA-256 of every file, the lock-file digest, and who registered
  it and when.
* Before every start, the gateway downloads the artifact (or uses its local
  cache), verifies its digest, re-validates its structure, extracts it, and
  checks every extracted file against the recorded manifest. Any mismatch
  stops the start with `integrity check failed` or `checksum mismatch`.
  Nothing is extracted or run from a corrupt artifact.
* `openmcp package verify NAME VERSION` (`POST …/verify`) re-checks a stored
  version on demand.

## Storage backends

| What | Backend | Settings |
| --- | --- | --- |
| Artifacts | `local` (default): a directory; write-once via atomic hard links | `packages.artifacts.path` |
| Artifacts | `s3`: any S3-compatible store (AWS S3, MinIO, Ceph, …); write-once via conditional `If-None-Match: *` plus a pre-check | `bucket`, `prefix`, `endpoint_url`, `region`, `access_key_id_env`, `secret_access_key_env` (names of env vars; omit both to use the AWS credential chain) |
| Catalog | `sqlite` (default) | `packages.catalog.path` |
| Catalog | `mongodb`: unique index on `(name, version)` | `mongodb_uri_env` (name of the env var with the connection string), `database`, `collection` |

Install the optional clients with `pip install 'openmcp-gateway[s3,mongodb]'`
(the Docker image includes both). Artifact keys are
`<prefix><name>/<version>/<digest>.zip`, so artifacts never overwrite each
other.

Local backends and SQLite are process-local: run one gateway per directory/
database file. With S3 and MongoDB, several gateways can share the same
package store and catalog. Server definitions and runtime state are still
per gateway (see [deployment.md](deployment.md#scaling-and-multiple-replicas)).

## Registering packages

```bash
export OPENMCP_ADMIN_TOKEN=<admin token>
openmcp package validate ./weather-mcp            # local check, nothing uploaded
openmcp package upload ./weather-mcp              # a directory (zipped client-side, symlinks refused)
openmcp package upload ./weather-mcp-1.0.0.zip    # or a ZIP
openmcp package list
openmcp package versions weather-mcp
openmcp package files weather-mcp 1.0.0
```

**Directory registration** (`openmcp package register-dir PATH`) makes the
*gateway* read a directory on its own host. It is for trusted local
development only, and requires `packages.allow_directory_registration: true`
and a path under one of `packages.directory_roots`. Symlinks are refused.

Registration never runs anything.

## Running a version

Running a package is an explicit step: create a server that references an
exact version.

```bash
openmcp package deploy weather-mcp 1.0.0 --id weather              # creates and starts server "weather"
openmcp package deploy weather-mcp 2.0.0 --id weather-next --network deny
openmcp server stop weather
openmcp server start weather
```

Or declare it in the configuration file or via `POST /admin/servers`:

```yaml
servers:
  - id: weather
    transport: package
    package: weather-mcp
    version: 1.0.0
    network: allow           # or deny
    env_from_host: {WEATHER_API_KEY: WEATHER_API_KEY}
```

Different versions, and different servers of the same version, run
independently: each start gets its own directory, process and environment
variables. Tools are namespaced by server id (`weather__forecast`,
`weather-next__forecast`).

### Runtime layout

```text
<runtime_dir>/<server id>/<random>/     mode 0700, new for every start
    pkg/     read-only copy of the package: the working directory and first on sys.path
    data/    writable: $HOME and $OPENMCP_PACKAGE_DATA
    tmp/     writable: $TMPDIR
```

The entrypoint is launched as
`<env>/bin/python -I -B <bootstrap.py> <spec>` (an argument vector, no
shell). Isolated mode (`-I`) means `PYTHONPATH`, `PYTHONHOME` and the user
site directory have no effect; the bootstrap puts `pkg/` first on `sys.path`
and runs the entrypoint as `__main__`, so `from tools.weather import …` works.
The process also receives `OPENMCP_PACKAGE_ROOT`, `OPENMCP_PACKAGE_NAME` and
`OPENMCP_PACKAGE_VERSION`, plus the usual minimal environment and the server's
`env`/`env_from_host`. Bytecode is not written to the read-only package tree.

The instance directory is deleted when the server stops, when its process
exits unexpectedly (before any restart, which uses a new directory), and when
a start fails part-way.

### Isolation

With `packages.sandbox.isolation: landlock` (the default), every package
process, and every process it spawns, is confined before the entrypoint runs.
It is a defence-in-depth sandbox for reducing the impact of a buggy or
malicious package. It is **not** a substitute for running untrusted code in a
separate VM, container or user account; see the limitations below.

**Filesystem (Landlock).**

| Path | Access |
| --- | --- |
| `/usr`, `/lib*`, `/bin`, `/sbin`, the Python installation, the package's environment, the gateway's packages (if inherited), `pkg/` | read and execute |
| selected `/etc` files: dynamic loader cache, TLS trust stores, `resolv.conf`, `hosts`, `nsswitch.conf`, `passwd`, `group`, time zone, OS release files | read |
| the instance's `data/` and `tmp/`, `/dev/null` | read and write |
| everything else: other instances, the artifact store, cache and catalog, the gateway's configuration (also under `/etc`), `/proc`, `/tmp`, home directories | **denied** |

Add paths with `packages.sandbox.extra_read_paths` if a package needs more.

**System calls (seccomp).** Creating `AF_UNIX` sockets is denied. Landlock
before ABI 6 does not control *connecting* to socket files, so without this
filter a package could talk to the Docker socket, D-Bus or a local database
socket whenever the gateway's user can. `socketpair()` stays allowed, which
asyncio needs. Path-based `truncate()` is denied on kernels whose Landlock
cannot control it (ABI < 3). Syscalls made through a foreign ABI (i386/x32 on
x86-64) kill the process. Supported architectures: x86-64 and aarch64;
elsewhere the start fails unless `block_unix_sockets: false`.

**Other controls.** `no_new_privs` (setuid binaries gain nothing). A
sandboxed process cannot `ptrace` processes outside its sandbox. Resource
limits: open files and file size always; optional `max_memory_bytes`,
`max_cpu_seconds` and `max_processes`. The gateway's environment variables
are not inherited.

**Network.** `network: deny` blocks all TCP connect and bind (Landlock ABI 4,
Linux 6.7+). With `allow`, the process can reach anything the host can,
including services on `localhost`. See the limitations.

#### Kernel support

| Landlock ABI | Linux | What it adds here |
| --- | --- | --- |
| 1 | 5.13 | filesystem rules (seccomp also denies `truncate()`) |
| 2 | 5.19 | rename/link across directories controlled |
| 3 | 6.2 | `truncate()` controlled by Landlock |
| 4 | 6.7 | `network: deny` available |
| 5 | 6.10 | device `ioctl` controlled |
| 6 | 6.12 | signals and abstract UNIX sockets scoped to the sandbox (enabled automatically) |

Verified in this project on Linux 6.8 (ABI 4), on the host and inside the
project's Docker image with Docker's default seccomp profile, a read-only
root filesystem and all capabilities dropped.

#### Limitations: read before running untrusted packages

* **Same OS user.** Package processes run as the gateway's user. On kernels
  before 6.12 (Landlock ABI < 6) a package can send signals to any process
  of that user, including the gateway and other servers. Run the gateway as a
  dedicated, unprivileged user, and never as a member of the `docker` group.
* **Network access is broad with `network: allow`.** A package can reach:
  * cloud metadata services (`169.254.169.254`), and so the host's cloud
    credentials, including any role that can read your S3 artifacts;
  * unauthenticated services on `localhost`, such as a MongoDB without auth;
  * this gateway's own MCP endpoint. With `auth.mode: none`, that means every
    other server's tools.

  Use `network: deny` where possible, `auth.mode: bearer`, IMDSv2 with a hop
  limit of 1 (or no instance role), authenticated databases, and network
  policies or a separate network namespace.
* **No CPU or memory limits by default.** The optional rlimits are
  per-process; `RLIMIT_NPROC` counts *all* processes of the user. For real
  quotas (CPU, memory, PIDs, disk space) run the gateway in a container or
  cgroup with limits. Disk usage of `data/`/`tmp/` is only bounded per file
  (`max_file_size_bytes`), not in total.
* **UDP is not restricted** by `network: deny` (Landlock controls TCP only).
* **Reads of system files are allowed.** Anything world-readable under `/usr`
  and the allowed `/etc` files can be read.
* **Linux only.** Elsewhere starts fail unless `isolation: none`, which
  provides **no isolation at all** (trusted development machines only).

## Dependencies

| `packages.dependencies.policy` | Behaviour |
| --- | --- |
| `locked` (default) | A package with dependencies must ship a lock file whose every line is `name==version` with at least one `--hash=sha256:`. Options such as `--index-url`, `-e` or `-r` are rejected. |
| `none` | Packages that declare dependencies are rejected. |

Generate a lock with `pip-compile --generate-hashes` or
`uv pip compile --generate-hashes requirements.txt -o requirements.lock`.

Installation is restricted. pip runs with `--require-hashes`,
`--only-binary :all:` (wheels only, so no package build scripts run),
`--no-deps`, and only the operator's index settings (`index_url`,
`find_links`, `no_index` for offline wheelhouses). It runs inside the same
Landlock sandbox, with writes confined to the environment being built, and is
killed after `install_timeout` seconds. `allowed_projects` restricts which
distributions may be installed at all.

Environments live in `<cache_dir>/envs/<key>`. The key is a digest of the
Python version, the lock-file digest and the dependency settings. They are
built once, atomically, and shared by every version with the same key.

With `inherit_gateway_packages: true` (default), the gateway's own installed
packages (including the MCP SDK) are visible to package servers *after* the
package's locked dependencies, so most servers need no installation at all.
Set it to `false` to give packages only what their lock file provides; the
lock must then include the MCP SDK.

## Deleting versions

`openmcp package delete NAME VERSION` (`DELETE /admin/packages/NAME/VERSION`):

* refused while any server definition references the version (409);
* refused for the newest `packages.retention.keep_latest` versions (403);
* refused entirely when `packages.retention.allow_delete: false` (403).

Deleting removes the catalog record, then the stored artifact. Cached copies
and environments are not garbage-collected yet (**planned**).

## Admin API

All endpoints require an `admin`-role token. Uploads are raw request bodies
with `Content-Type: application/zip`.

| Method and path | Purpose |
| --- | --- |
| `POST /admin/packages/validate` | validate only; nothing stored or run |
| `POST /admin/packages` | upload and register (201 new, 200 identical, 409 conflict, 413 too large, 422 invalid) |
| `POST /admin/packages/register-directory` | `{"path": …}` (development) |
| `GET /admin/packages` | packages and their versions |
| `GET /admin/packages/{name}` | versions with digest, size, file count, `used_by` |
| `GET /admin/packages/{name}/{version}` | full metadata |
| `GET /admin/packages/{name}/{version}/files` | file manifest (path, size, sha256); contents are not served |
| `POST /admin/packages/{name}/{version}/verify` | integrity check (200 ok, 409 failed) |
| `POST /admin/packages/{name}/{version}/deploy` | create (and optionally start) a server |
| `DELETE /admin/packages/{name}/{version}` | delete per retention policy |

Server lifecycle uses the existing endpoints (`/admin/servers/{id}/start|stop|restart`,
`GET /admin/servers/{id}` shows the running version, digest, environment and
instance directory). Registration, deployment and deletion are written to the audit logger
(`openmcp_gateway.audit`).
