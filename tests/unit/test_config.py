from __future__ import annotations

from pathlib import Path

import pytest

from openmcp_gateway.config import ConfigError, GatewayConfig, load_config

HASH = "a" * 64


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_defaults_are_local_and_unauthenticated() -> None:
    cfg = GatewayConfig()
    assert cfg.gateway.host == "127.0.0.1"
    assert cfg.auth.mode == "none"
    assert cfg.admin.allow_stdio_registration is False
    assert cfg.servers == []


def test_loads_yaml(tmp_path: Path) -> None:
    cfg = load_config(
        write(
            tmp_path,
            "servers:\n  - id: files\n    command: npx\n    args: ['-y', 'server-filesystem', '/tmp']\n",
        )
    )
    assert cfg.servers[0].id == "files"
    assert cfg.servers[0].args == ["-y", "server-filesystem", "/tmp"]


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENMCP_GATEWAY__PORT", "9999")
    cfg = load_config(write(tmp_path, "gateway:\n  port: 8000\n  name: X\n"))
    assert cfg.gateway.port == 9999
    assert cfg.gateway.name == "X"  # untouched sibling keys survive


def test_non_loopback_without_auth_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="refusing to listen"):
        load_config(write(tmp_path, "gateway:\n  host: 0.0.0.0\n"))


def test_non_loopback_without_auth_requires_explicit_override(tmp_path: Path) -> None:
    cfg = load_config(
        write(tmp_path, "gateway:\n  host: 0.0.0.0\nauth:\n  allow_unauthenticated_non_loopback: true\n")
    )
    assert any("authentication is disabled" in w for w in cfg.warnings())


def test_bearer_requires_tokens(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="requires at least one token"):
        load_config(write(tmp_path, "auth:\n  mode: bearer\n"))


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("servers:\n  - id: Bad_Id\n    command: x\n", "server id must match"),
        ("servers:\n  - id: a\n    command: x\n  - id: a\n    command: y\n", "duplicate server ids"),
        ("servers:\n  - id: a\n    command: ''\n", "at least 1 character"),
        ("servers:\n  - id: a\n    command: x\n    env: {'BAD-NAME': v}\n", "invalid environment variable"),
        ("servers:\n  - id: a\n    command: x\n    unknown_key: 1\n", "Extra inputs are not permitted"),
        ("auth:\n  tokens:\n    - {name: a, sha256: notahash}\n", "64 hex characters"),
        (
            f"auth:\n  tokens:\n    - {{name: a, sha256: {HASH}}}\n    - {{name: b, sha256: {HASH}}}\n",
            "unique",
        ),
        ("servers: {}\n", "valid list"),
        ("- just a list\n", "top level must be a mapping"),
        ("a: [unclosed\n", "invalid YAML"),
        ("gateway:\n  mcp_path: /admin/mcp\n", "must not be"),
        ("gateway:\n  mcp_path: /\n", "must not be"),
    ],
)
def test_invalid_configs_have_readable_errors(tmp_path: Path, snippet: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, snippet))


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")


def test_nul_in_args_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="NUL"):
        load_config(write(tmp_path, 'servers:\n  - id: a\n    command: x\n    args: ["a\\0b"]\n'))


def test_warning_for_unknown_server_in_token(tmp_path: Path) -> None:
    cfg = load_config(
        write(tmp_path, f"auth:\n  tokens:\n    - {{name: a, sha256: {HASH}, servers: [ghost]}}\n")
    )
    assert any("unknown server 'ghost'" in w for w in cfg.warnings())


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.parametrize("path", [*sorted(EXAMPLES.glob("*.yaml")), EXAMPLES / "docker" / "openmcp.yaml"])
def test_example_gateway_configs_are_valid(path: Path) -> None:
    load_config(path)


@pytest.mark.parametrize("path", sorted((EXAMPLES / "servers").glob("*.yaml")))
def test_example_server_definitions_are_valid(path: Path) -> None:
    import yaml

    from openmcp_gateway.storage.base import SERVER_ADAPTER

    SERVER_ADAPTER.validate_python(yaml.safe_load(path.read_text()))


@pytest.mark.parametrize("path", sorted((EXAMPLES / "packages").iterdir()))
def test_example_packages_are_valid(path: Path) -> None:
    from openmcp_gateway.config import PackagesConfig
    from openmcp_gateway.packages.archive import archive_directory, inspect_archive, policy_from_config

    cfg = PackagesConfig()
    inspect_archive(archive_directory(path, cfg.limits), policy_from_config(cfg))


def test_warning_when_packages_run_without_mcp_auth() -> None:
    from openmcp_gateway.config import GatewayConfig

    cfg = GatewayConfig(packages={"enabled": True})
    assert any("loopback endpoint" in w for w in cfg.warnings())
