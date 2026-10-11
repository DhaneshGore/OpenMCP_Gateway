# 0004. Secure-by-default authentication posture

* Status: Accepted
* Date: 2026-10-11

## Context

A gateway that launches subprocesses and forwards tool calls is a high-value
target. Local development must stay frictionless, but a misconfigured remote
deployment must not become an open proxy or a remote-code-execution endpoint.

## Decision

* Default bind `127.0.0.1`; default `auth.mode: none` **only** permitted on a
  loopback bind. A non-loopback bind with `none` refuses to start unless
  `auth.allow_unauthenticated_non_loopback: true`.
* `auth.mode: bearer` uses opaque random tokens stored as SHA-256 hashes in the
  config. These are gateway API keys, **not** OAuth access tokens; OAuth 2.1
  resource-server support is a separate, planned authenticator.
* Admin API: always requires a bearer token with the `admin` role, even in
  `none` mode; disabled when no admin token exists. Registering stdio servers
  over the API is disabled unless `admin.allow_stdio_registration: true`.
* Forwarded identity headers (`X-Forwarded-User` etc.) are never trusted.
  Proxy IP headers are honoured only for explicitly configured proxy addresses.

## Consequences

Operators must generate a token (`openmcp token generate`) before remote use.
