# Architecture Decision Records

An ADR records one significant decision: the context, the options considered,
what we chose, and the consequences. ADRs are immutable once accepted; a later
ADR may supersede an earlier one.

## Process

1. Copy `template.md` to `NNNN-short-title.md` (next free number).
2. Open a pull request with status `Proposed`. Discussion happens on the PR.
3. A maintainer merges it as `Accepted` (or closes it as `Rejected`).

Write an ADR when a change affects protocol behaviour, security defaults,
public configuration/API compatibility, the storage schema, or adds a major
dependency.

| # | Title | Status |
| --- | --- | --- |
| 0000 | [MVP acceptance criteria](0000-mvp-acceptance.md) | Accepted |
| 0001 | [Use the official MCP SDK for the client-facing endpoint](0001-sdk-for-client-facing-endpoint.md) | Accepted |
| 0002 | [Own JSON-RPC multiplexer for stdio upstreams](0002-own-stdio-multiplexer.md) | Accepted |
| 0003 | [Tool namespacing with `server__tool`](0003-tool-namespacing.md) | Accepted |
| 0004 | [Secure-by-default authentication posture](0004-auth-defaults.md) | Accepted |
| 0005 | [Validate remote upstream destinations at connect time](0005-connect-time-egress-guard.md) | Accepted |
| 0006 | [Multi-file MCP server packages](0006-multi-file-packages.md) | Accepted |
