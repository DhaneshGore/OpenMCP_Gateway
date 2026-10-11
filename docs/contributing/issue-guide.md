# Issues, labels and triage

## Labels

| Label | Meaning |
| --- | --- |
| `bug` | Behaviour differs from documentation |
| `enhancement` | New capability or improvement |
| `documentation` | Docs only |
| `security` | Security hardening (vulnerabilities go through SECURITY.md, not issues) |
| `good first issue` | Well scoped, with pointers to files and tests; no deep context needed |
| `help wanted` | Maintainers would welcome a contributor taking this |
| `needs-triage` | New, not yet reviewed |
| `needs-adr` | Requires an architecture decision record before code |
| `area/protocol`, `area/runtime`, `area/security`, `area/admin`, `area/cli`, `area/docs` | Component |

## Triage

New issues get `needs-triage`. A maintainer reproduces or clarifies the issue,
adds area and type labels, and removes `needs-triage`. Proposals that change
protocol behaviour, security defaults, public configuration or the storage
schema get `needs-adr`.

## Writing a good first issue

A good first issue states the goal, the files involved, how to test it, and
what "done" looks like. It needs no design decisions. Candidate issues for
the initial backlog (create them on GitHub when the repository is public):

1. **Forward `resources/templates/list`.** Aggregate templates from visible
   servers in `gateway.py` (mirror `list_resources`), register
   `on_list_resource_templates` in `downstream/server.py`, add the method to
   `upstream/http.py` dispatch, and extend the fake server and e2e tests.
2. **`openmcp server list --json` for other subcommands.** Add `--json` to
   `tools` and `logs` in `cli.py`; test in `tests/e2e/test_cli.py`.
3. **Expose active session count as a metric.** Needs a small, public way to
   count sessions; investigate the SDK's session manager API first.
4. **Example config for tool filters.** Add an
   `examples/` config showing tool filters (`servers[].tools.allow/deny`) with
   a test in `tests/unit/test_config.py` that loads it.
5. **`openmcp token list`.** Print token names, server lists and roles from
   the config (never hashes).
6. **Troubleshooting entries.** Add entries for problems you hit while
   setting up a real server (with the server's name and the fix).

Larger `help wanted` items are listed in [ROADMAP.md](../../ROADMAP.md).
