## What and why

<!-- What does this change, and what problem does it solve? Link issues: "Fixes #123". -->

## How was it tested?

<!-- New/changed tests, manual steps. Behaviour changes need a test that fails without the change. -->

## Checklist

- [ ] `ruff format`, `ruff check`, `mypy` and `pytest` pass locally
- [ ] Docs updated (and `python scripts/gen_config_reference.py` run if config models changed)
- [ ] `CHANGELOG.md` updated under **Unreleased** for user-visible changes
- [ ] New settings default to the safe option; no secrets, tool arguments or results are logged
- [ ] New buffers/queues/loops are bounded
- [ ] ADR added if this changes protocol behaviour, security defaults, public config/API, storage schema, or adds a major dependency
