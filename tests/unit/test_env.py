from __future__ import annotations

import pytest

from openmcp_gateway.errors import UpstreamStartError
from openmcp_gateway.security.env import build_child_env
from tests.conftest import fake_server_config

HOST = {
    "PATH": "/usr/bin",
    "HOME": "/home/u",
    "AWS_SECRET_ACCESS_KEY": "do-not-leak",
    "GITHUB_TOKEN": "ghp_token_value",
    "EXTRA": "e",
}


def test_gateway_environment_is_not_inherited() -> None:
    env, secrets = build_child_env(fake_server_config(), HOST)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/u"}
    assert secrets == []


def test_explicit_values_inherit_and_secret_references() -> None:
    cfg = fake_server_config(
        env={"MODE": "x"}, inherit_env=["EXTRA"], env_from_host={"TOKEN": "GITHUB_TOKEN"}
    )
    env, secrets = build_child_env(cfg, HOST)
    assert env["MODE"] == "x"
    assert env["EXTRA"] == "e"
    assert env["TOKEN"] == "ghp_token_value"
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert secrets == ["ghp_token_value"]


def test_missing_secret_reference_names_variable_not_value() -> None:
    cfg = fake_server_config(env_from_host={"TOKEN": "MISSING_VAR"})
    with pytest.raises(UpstreamStartError, match="MISSING_VAR"):
        build_child_env(cfg, HOST)
