# Contributing

Thanks for helping. Bug reports, docs fixes, tests, and features are all
welcome. This guide explains how to get set up and what reviewers look for.

## Ground rules

* Be kind. See the [Code of Conduct](CODE_OF_CONDUCT.md).
* Report security problems privately ([SECURITY.md](SECURITY.md)), never in
  a public issue.
* For anything bigger than a small fix, open an issue first so we can agree on
  the approach. Changes to protocol behaviour, security defaults, public
  configuration or the storage schema need an
  [ADR](docs/architecture/decision-records/README.md).
* Only contribute code you have the right to contribute under the Apache-2.0
  license. Don't copy code from projects with incompatible licenses or from
  proprietary sources.

## Development setup

```bash
git clone https://github.com/DhaneshGore/mcp-bridge.git
cd mcp-bridge
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Python 3.11+ is required. CI tests 3.11 through 3.14 on Linux.

## Before you push

```bash
ruff format src tests scripts       # formatting
ruff check src tests scripts        # lint (includes security rules)
mypy                                # strict type checking
pytest                              # full suite (~30 s; launches real subprocesses)
python scripts/gen_config_reference.py   # if you changed config models
```

`pytest -m "not e2e"` runs only the fast unit tests. MongoDB tests run when
`OPENMCP_TEST_MONGODB_URI` is set (e.g. `docker run -p 27017:27017 mongo:7`).
Sandbox tests skip on kernels without Landlock; CI sets
`OPENMCP_REQUIRE_LANDLOCK=1` so that they fail instead of being skipped. The optional load
tests run with `pytest -m load -s tests/load` and are never required.

## Project layout

```text
src/openmcp_gateway/
  config.py        configuration models (docs are generated from them)
  app.py           FastAPI application factory
  cli.py           the `openmcp` command
  gateway.py       routing, authorization, aggregation (transport-free)
  downstream/      client-facing MCP server (SDK lowlevel Server)
  upstream/        jsonrpc.py (multiplexer), stdio.py, http.py, base.py (interface)
  runtime/         ServerManager: lifecycle, restarts, concurrency
  auth/            principals and authenticators
  security/        SSRF guard, env allowlist, rate limiting
  storage/         registry interface, memory and SQLite adapters
  packages/        multi-file packages: archive, artifacts, catalog, environments, sandbox, runtime
  observability/   Prometheus metrics
  api/             health, admin API, middleware
tests/
  unit/            fast, no I/O
  integration/     real subprocesses / sockets
  e2e/             full gateway over HTTP with the SDK client
  fixtures/fake_server.py   scripted misbehaving MCP server
  load/            optional load tests
```

[ARCHITECTURE.md](ARCHITECTURE.md) explains how the pieces fit together.

## What reviewers look for

* **Tests.** Every behaviour change comes with a test that fails without it.
  Concurrency and failure paths deserve explicit tests; the fake server in
  `tests/fixtures/fake_server.py` makes misbehaviour easy to script.
* **Honest docs.** Update docs in the same PR. Don't document planned work as
  if it exists.
* **Security posture.** New options must default to the safe choice. Never
  log secrets, tool arguments or results; never put exception text into client
  errors (`errors.py` explains the error model).
* **Bounded resources.** Every new buffer, queue, cache or loop needs a limit.
* **Small, focused PRs** with a clear description of why.

At least one maintainer approval and green CI are required to merge. We aim
to give a first response within a week. If you hear nothing, it's fine to
ping the PR.

## Extension points

These interfaces are where new functionality should plug in. They are still
0.x and may change, but changes will be noted in the changelog:

| To add… | Implement | See |
| --- | --- | --- |
| an upstream transport | `upstream.base.UpstreamConnection` | `upstream/stdio.py`, `upstream/http.py` |
| an authentication method | `auth.backends.Authenticator` | `StaticTokenAuthenticator` |
| a storage backend | `storage.base.ServerRepository` | `storage/sqlite.py` |
| a package artifact store | `packages.artifacts.ArtifactStore` | `LocalArtifactStore`, `S3ArtifactStore` |
| a package catalog | `packages.catalog.PackageCatalog` | `SqlitePackageCatalog`, `MongoPackageCatalog` |
| a routing scheme | `routing/namespace.py` | ADR 0003 |

Extensions are ordinary code reviewed into this repository. The gateway
never discovers or executes plugins automatically.

## Finding something to work on

Issues labelled [`good first issue`](https://github.com/DhaneshGore/mcp-bridge/labels/good%20first%20issue)
are scoped to be approachable without deep knowledge of the codebase; each
says which files are involved and how to test the change. `help wanted`
issues are larger. See [docs/contributing/issue-guide.md](docs/contributing/issue-guide.md)
for labels and triage.

## Commit and PR conventions

* Write commit subjects in the imperative ("Add …", "Fix …"), 72 characters max.
* Reference issues in the PR description (`Fixes #123`).
* Add a line to `CHANGELOG.md` under **Unreleased** for user-visible changes.
