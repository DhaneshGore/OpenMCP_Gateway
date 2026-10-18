# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the
version is 0.x, minor releases may contain breaking changes; they will be
called out under **Changed** or **Removed**.

## [Unreleased]

Initial development. Nothing has been released yet.

### Fixed

- Package servers that depended on Python packages installed with
  `pip install --user` failed to start because `gateway_site_paths()` did not
  include the user site-packages directory in the sandboxed venv's `.pth`
  file. The function now includes the user site-packages directory when it is
  present on `sys.path`.
- Removed `watchfiles` from the `dev` optional dependencies; it requires a
  Rust toolchain at install time and is only needed for the `hot-reload`
  optional feature. `pip install -e ".[dev]"` now succeeds on any Python
  version without a Rust compiler.

### Added

- Streamable HTTP MCP endpoint built on the official MCP Python SDK
  (protocol revisions 2024-11-05 to 2025-11-25 and 2026-07-28).
- stdio upstream servers with an id-correlated JSON-RPC multiplexer, bounded
  message size, bounded stderr capture, and process-group shutdown.
- Remote Streamable HTTP upstream servers via the SDK client, with
  connect-time SSRF protection.
- Forwarding of tools, prompts, resources and progress notifications with
  `server__name` namespacing.
- Server lifecycle management: single-flight start, crash detection, restart
  with exponential backoff and a hard cap, lazy start, operator stop/start.
- Per-server concurrency limits with bounded queues; in-process rate limits.
- Bearer-token authentication (hashed tokens), per-principal server
  allowlists, admin and metrics roles, sessions bound to their creator.
- Admin API and `openmcp server …` commands; Prometheus metrics; JSON logs
  with secret redaction; `/health`, `/ready`, `/version`.
- Runtime server registry with in-memory and SQLite backends.
- `openmcp` CLI: `serve`, `config validate|show`, `token generate|hash`,
  `doctor`, `server list|inspect|start|stop|restart|tools|logs|add|remove`.
- Docker image and compose demo.
- Multi-file MCP server packages: ZIP/directory registration with strict
  validation (traversal, symlinks, duplicates, zip bombs, depth, file types),
  immutable content-addressed versions, local or S3-compatible artifact
  storage, SQLite or MongoDB catalog, digest and manifest verification before
  every start, hash-locked wheel-only dependency environments, per-instance
  runtime directories, Landlock filesystem/network isolation, retention-aware
  deletion, admin API (`/admin/packages`) and `openmcp package …` commands.
- Package sandbox hardening from the release review: a seccomp filter
  denying AF_UNIX socket creation (closes access to e.g. the Docker socket),
  path-based `truncate()` on older kernels, and foreign-ABI syscalls; `/etc`
  narrowed to an allowlist; optional memory/CPU/process rlimits; cleanup of
  runtime directories left by a crashed gateway; a warning when packages run
  with unauthenticated MCP access.
