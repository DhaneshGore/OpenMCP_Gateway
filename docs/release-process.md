# Release process

The project follows [Semantic Versioning](https://semver.org). Before 1.0,
minor versions may contain breaking changes, which are always listed in the
changelog.

## Checklist

Maintainers only. Nothing is published without an explicit maintainer decision.

1. **Freeze.** Make sure `main` is green, and decide the version (`X.Y.Z`).
2. **Verify from scratch** in a fresh Codespace or container:
   ```bash
   python -m venv /tmp/rel && . /tmp/rel/bin/activate
   pip install .
   openmcp --config examples/openmcp.yaml doctor
   pip install -e ".[dev]" && pytest && ruff check src tests scripts && mypy
   pytest -m load -s tests/load        # optional, sanity only
   docker build -t openmcp-gateway:rc . && docker compose up -d && curl -fsS 127.0.0.1:8080/ready
   ```
   Walk through every command in README.md and docs/getting-started.md.
3. **Dependencies.** Regenerate the inventory in `docs/dependencies.md`, run
   `pip-audit`, and review the dependency diff since the last release.
4. **Security review.** Read the diff of `security/`, `auth/`, `upstream/`,
   `api/admin.py` and `config.py` defaults since the last release.
5. **Docs.** Make sure `ROADMAP.md` status markers, the README feature table
   and `docs/transports.md` match what ships; move CHANGELOG entries from
   **Unreleased** to `## [X.Y.Z] - YYYY-MM-DD`.
6. **Version.** Set `__version__` in `src/openmcp_gateway/__init__.py`.
7. **Tag.** `git tag -s vX.Y.Z -m "vX.Y.Z"` and push the tag.
8. **Publish.** Create the GitHub release from the changelog section. PyPI
   and container registry publishing are not set up yet; when they are,
   they should run from CI with trusted publishing, never from a laptop.
9. **Announce.** Only after the release artifacts are verified.

## Hotfixes

Branch from the release tag, apply the minimal fix with a test, bump the patch
version, and follow the checklist from step 2.
