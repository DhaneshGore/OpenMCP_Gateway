# 0006. Multi-file MCP server packages

* Status: Accepted
* Date: 2026-10-11

## Context

Real MCP servers are not a single `server.py`: they import sibling modules
and nested packages and read schemas, templates and configuration at runtime.
Operators need to upload such servers, keep exact versions, and run them
safely next to each other. Uploads are untrusted input, and running a package
means running its code.

## Decisions

1. **Unit of deployment: an immutable, content-addressed version.** Uploads
   are validated (paths, entry types, sizes, ratios, depth, duplicates,
   extension allowlist, metadata, syntax) and re-packed into a canonical
   archive (sorted, fixed timestamps, stored) whose SHA-256 is the version's
   identity. Same version with different content is a conflict.
2. **Storage behind two interfaces.** `ArtifactStore` (local directory or any
   S3-compatible store, write-once) and `PackageCatalog` (SQLite or MongoDB,
   unique `(name, version)`). S3 and MongoDB are first-class, but the original
   project requirement says no hosted service may be mandatory for local
   development, so the defaults are local and SQLite.
3. **Verify before every run.** Digest check of the artifact, re-validation,
   and a per-file manifest check during extraction. The verified tree is
   extracted read-only into a fresh per-start instance directory, which is
   removed on stop, crash and failed start.
4. **Isolation with Landlock.** A stdlib-only bootstrap confines each package
   process to read-only system/interpreter/package paths and its own writable
   data/tmp directories, optionally without TCP. It needs no root, no extra
   binaries, and works in unprivileged containers. Alternatives:
   bubblewrap or user namespaces (not available everywhere, often disabled in
   containers); separate UIDs (need root); containers per server (heavy, needs
   a container runtime).
5. **Dependencies: hash-pinned wheels only.** `--require-hashes`,
   `--only-binary :all:`, `--no-deps`, operator-controlled index, timeout,
   sandboxed install. Environments are cached by a digest of Python version,
   lock digest and settings. By default the gateway's own packages (the MCP
   SDK) are visible after the locked dependencies.
6. **Explicit execution.** Registration never runs code. A server definition
   (config, admin API or `deploy`) references an exact version.

## Consequences

* Package servers require Linux with Landlock unless isolation is explicitly
  disabled.
* Processes share the gateway's UID. Signal isolation needs Landlock ABI 6;
  CPU/memory limits are left to the deployment.
* Packages that need native extensions must get them as wheels through the
  lock file; source builds are impossible by design.
* Cache garbage collection is future work.
