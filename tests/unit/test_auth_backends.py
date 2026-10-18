"""Unit tests for auth/backends.py."""

from __future__ import annotations

import pytest

from openmcp_gateway.auth.backends import (
    TOKEN_PREFIX,
    OptionalTokenAuthenticator,
    StaticTokenAuthenticator,
    bearer_token,
    generate_token,
    hash_token,
)
from openmcp_gateway.auth.principal import ANONYMOUS
from openmcp_gateway.config import AuthConfig

HASH_A = "a" * 64
HASH_B = "b" * 64


def _config(**kwargs: object) -> AuthConfig:
    return AuthConfig.model_validate({"mode": "bearer", **kwargs})


def _auth_config_with_tokens() -> AuthConfig:
    token = generate_token()
    return AuthConfig.model_validate(
        {
            "mode": "bearer",
            "tokens": [{"name": "alice", "sha256": hash_token(token), "servers": ["s1"]}],
        }
    ), token


# ------------------------------------------------------------------ generate_token


def test_generate_token_has_prefix() -> None:
    assert generate_token().startswith(TOKEN_PREFIX)


def test_generate_token_is_unique() -> None:
    assert generate_token() != generate_token()


def test_hash_token_is_64_hex_chars() -> None:
    h = hash_token(generate_token())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ------------------------------------------------------------------ bearer_token


class _FakeConn:
    def __init__(self, auth_header: str | None) -> None:
        self.headers: dict[str, str] = {}
        if auth_header is not None:
            self.headers["authorization"] = auth_header


def test_bearer_token_extracted() -> None:
    assert bearer_token(_FakeConn("Bearer mytoken")) == "mytoken"  # type: ignore[arg-type]


def test_bearer_token_case_insensitive_scheme() -> None:
    assert bearer_token(_FakeConn("BEARER mytoken")) == "mytoken"  # type: ignore[arg-type]


def test_bearer_token_missing_header_returns_none() -> None:
    assert bearer_token(_FakeConn(None)) is None  # type: ignore[arg-type]


def test_bearer_token_non_bearer_scheme_returns_none() -> None:
    assert bearer_token(_FakeConn("Basic dXNlcjpwYXNz")) is None  # type: ignore[arg-type]


def test_bearer_token_empty_value_returns_none() -> None:
    assert bearer_token(_FakeConn("Bearer ")) is None  # type: ignore[arg-type]


# ------------------------------------------------------------------ StaticTokenAuthenticator


@pytest.mark.asyncio
async def test_static_token_authenticates_known_token() -> None:
    token = generate_token()
    cfg = AuthConfig.model_validate(
        {"mode": "bearer", "tokens": [{"name": "alice", "sha256": hash_token(token), "servers": ["*"]}]}
    )
    auth = StaticTokenAuthenticator(cfg)
    principal = await auth.authenticate(_FakeConn(f"Bearer {token}"))  # type: ignore[arg-type]
    assert principal is not None
    assert principal.name == "alice"


@pytest.mark.asyncio
async def test_static_token_rejects_unknown_token() -> None:
    token = generate_token()
    other_token = generate_token()
    cfg = AuthConfig.model_validate(
        {"mode": "bearer", "tokens": [{"name": "alice", "sha256": hash_token(token)}]}
    )
    auth = StaticTokenAuthenticator(cfg)
    assert await auth.authenticate(_FakeConn(f"Bearer {other_token}")) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_static_token_rejects_token_exceeding_512_bytes() -> None:
    real_token = generate_token()
    cfg = AuthConfig.model_validate(
        {"mode": "bearer", "tokens": [{"name": "t", "sha256": hash_token(real_token)}]}
    )
    auth = StaticTokenAuthenticator(cfg)
    long_token = "x" * 513
    assert await auth.authenticate(_FakeConn(f"Bearer {long_token}")) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_static_token_rejects_missing_header() -> None:
    real_token = generate_token()
    cfg = AuthConfig.model_validate(
        {"mode": "bearer", "tokens": [{"name": "t", "sha256": hash_token(real_token)}]}
    )
    auth = StaticTokenAuthenticator(cfg)
    assert await auth.authenticate(_FakeConn(None)) is None  # type: ignore[arg-type]


def test_static_token_reload_updates_table() -> None:
    token_a = generate_token()
    token_b = generate_token()
    cfg_a = AuthConfig.model_validate(
        {"mode": "bearer", "tokens": [{"name": "alice", "sha256": hash_token(token_a)}]}
    )
    cfg_b = AuthConfig.model_validate(
        {"mode": "bearer", "tokens": [{"name": "bob", "sha256": hash_token(token_b)}]}
    )
    auth = StaticTokenAuthenticator(cfg_a)
    auth.reload(cfg_b)
    assert hash_token(token_a) not in auth._by_hash
    assert hash_token(token_b) in auth._by_hash


# ------------------------------------------------------------------ OptionalTokenAuthenticator


@pytest.mark.asyncio
async def test_optional_auth_no_token_returns_anonymous() -> None:
    cfg = AuthConfig.model_validate({"mode": "optional", "tokens": []})
    auth = OptionalTokenAuthenticator(cfg)
    principal = await auth.authenticate(_FakeConn(None))  # type: ignore[arg-type]
    assert principal is ANONYMOUS


@pytest.mark.asyncio
async def test_optional_auth_valid_token_returns_principal() -> None:
    token = generate_token()
    cfg = AuthConfig.model_validate(
        {"mode": "optional", "tokens": [{"name": "alice", "sha256": hash_token(token)}]}
    )
    auth = OptionalTokenAuthenticator(cfg)
    principal = await auth.authenticate(_FakeConn(f"Bearer {token}"))  # type: ignore[arg-type]
    assert principal is not None
    assert principal.name == "alice"


@pytest.mark.asyncio
async def test_optional_auth_invalid_token_does_not_downgrade_to_anonymous() -> None:
    """An unrecognised token must be rejected, not silently treated as anonymous.

    This prevents a misconfigured client from accidentally getting anonymous
    access by presenting an expired or wrong token.
    """
    cfg = AuthConfig.model_validate({"mode": "optional", "tokens": []})
    auth = OptionalTokenAuthenticator(cfg)
    result = await auth.authenticate(_FakeConn("Bearer unknown-token-xyz"))  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_optional_auth_oversized_token_rejected() -> None:
    cfg = AuthConfig.model_validate({"mode": "optional", "tokens": []})
    auth = OptionalTokenAuthenticator(cfg)
    assert await auth.authenticate(_FakeConn("Bearer " + "x" * 513)) is None  # type: ignore[arg-type]
