# 0005. Validate remote upstream destinations at connect time

* Status: Accepted
* Date: 2026-10-11

## Context

Remote upstream URLs come from operators and, through the admin API, from
admin-token holders. Without controls, the gateway could be pointed at
internal services or cloud metadata endpoints (SSRF). Validating the URL's
hostname once at configuration time does not help against DNS rebinding (the
name resolves to a public address during the check and to an internal one at
connect time) or against redirects.

## Options considered

1. Validate the URL when it is configured.
2. Resolve once, then pin the address by rewriting the URL to the IP. This
   breaks TLS hostname verification and virtual hosting.
3. Validate inside the HTTP client's network backend at every connection,
   dialing the validated address while keeping the original hostname for TLS
   and the Host header.

## Decision

Option 3. `security/ssrf.py` provides `GuardedBackend` (an
`httpcore2.AsyncNetworkBackend`) and `GuardedTransport`. httpx2 doesn't
expose a way to choose the network backend, so `GuardedTransport` replaces
the connection pool that `AsyncHTTPTransport` builds. A test asserts the
guard is in place, so an httpx2 change that breaks this hook fails CI
instead of silently removing the protection.

Policy: every resolved address must be globally routable, or inside
`security.allowed_egress_networks`, or the server sets
`allow_private_network`. IPv4 embedded in IPv6 is unwrapped. Proxy
environment variables are ignored for these clients.

## Consequences

* Remote upstreams cannot be reached through an HTTP proxy (yet).
* We depend on an httpx2 internal (`_pool`); guarded by a test.
