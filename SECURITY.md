# Security Policy

## Supported versions

The project is pre-1.0. Security fixes are made on the `main` branch and
included in the next release. Once 1.0 ships, this table will list supported
release lines.

| Version | Supported |
| --- | --- |
| `main` | yes |
| < 1.0 releases | latest release only |

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting:
the repository's **Security** tab → **Report a vulnerability**. If that isn't
available, open a minimal public issue asking a maintainer to contact you,
without any details.

Please include:

* affected version or commit,
* configuration needed to reproduce (with secrets removed),
* steps to reproduce and impact.

What to expect:

* acknowledgement within 5 working days;
* an assessment and, if confirmed, a fix plan within 30 days;
* credit in the release notes if you want it.

We follow coordinated disclosure: please give us a reasonable chance to
release a fix before publishing details.

## Scope

In scope: the gateway's own code, its default configuration, and the Docker
image built from this repository. Out of scope: vulnerabilities in upstream
MCP servers you connect, and deployments that disable documented protections
(for example `auth.allow_unauthenticated_non_loopback: true` on a public
network).

The threat model and hardening guidance are in
[docs/security.md](docs/security.md).
