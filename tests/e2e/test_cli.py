"""CLI tests: argument parsing, config commands, tokens, doctor and server commands."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from openmcp_gateway.auth.backends import generate_token, hash_token
from openmcp_gateway.cli import main
from tests.conftest import FAKE_SERVER, fake_server_config, gateway_config
from tests.e2e.harness import run_gateway

pytestmark = pytest.mark.e2e


def write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "openmcp.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def fake_entry(server_id: str = "f", mode: str = "normal") -> dict[str, Any]:
    return {"id": server_id, "command": sys.executable, "args": [str(FAKE_SERVER), mode]}


def test_config_validate_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"servers": [fake_entry()]})
    assert main(["--config", str(path), "config", "validate"]) == 0
    assert "1 server(s)" in capsys.readouterr().out


def test_config_validate_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"gateway": {"host": "0.0.0.0"}})
    assert main(["--config", str(path), "config", "validate"]) == 1
    assert "refusing to listen" in capsys.readouterr().err


def test_admin_token_gets_no_mcp_access_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token", "generate", "--name", "ops", "--admin"]) == 0
    out = capsys.readouterr().out
    entry = yaml.safe_load(next(line for line in out.splitlines() if line.startswith("- ")))[0]
    assert entry["servers"] == []
    assert entry["roles"] == ["admin"]


def test_token_generate_produces_matching_hash(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["token", "generate", "--name", "ci", "--server", "a", "--admin"]) == 0
    out = capsys.readouterr().out
    token = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("token: "))
    entry = yaml.safe_load(next(line for line in out.splitlines() if line.startswith("- ")))[0]
    assert entry == {"name": "ci", "sha256": hash_token(token), "servers": ["a"], "roles": ["admin"]}


def test_doctor_starts_servers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"servers": [fake_entry("good"), fake_entry("bad", "crash-on-start")]})
    assert main(["--config", str(path), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "ok   server good: started, protocol 2025-11-25" in out
    assert "FAIL server bad" in out


def test_doctor_reports_missing_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_config(tmp_path, {"servers": [{"id": "x", "command": "no-such-binary-zzz"}]})
    assert main(["--config", str(path), "doctor", "--no-start"]) == 1
    assert "not found" in capsys.readouterr().out


def test_server_commands_require_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["server", "--url", "http://127.0.0.1:9", "list"]) == 1
    assert "OPENMCP_ADMIN_TOKEN" in capsys.readouterr().err


async def test_server_commands_against_running_gateway(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    token = generate_token()
    cfg = gateway_config(
        [fake_server_config("f")],
        auth={"tokens": [{"name": "op", "sha256": hash_token(token), "roles": ["admin"]}]},
    )
    monkeypatch.setenv("OPENMCP_ADMIN_TOKEN", token)
    async with run_gateway(cfg) as gw:

        def cli(*argv: str) -> int:
            return main(["server", "--url", gw.base_url, *argv])

        assert await asyncio.to_thread(cli, "list") == 0
        assert re.search(r"^f\s+ready\s", capsys.readouterr().out, re.MULTILINE)
        assert await asyncio.to_thread(cli, "stop", "f") == 0
        assert capsys.readouterr().out.strip() == "f: stopped"
        assert await asyncio.to_thread(cli, "start", "f") == 0
        assert capsys.readouterr().out.strip() == "f: ready"
        assert await asyncio.to_thread(cli, "tools", "f") == 0
        assert "f__echo" in capsys.readouterr().out
        assert await asyncio.to_thread(cli, "inspect", "ghost") == 1
        assert "404" in capsys.readouterr().err
        # Registering stdio servers over the API is disabled by default.
        definition = tmp_path / "srv.yaml"
        definition.write_text(yaml.safe_dump(fake_entry("dyn")))
        assert await asyncio.to_thread(cli, "add", str(definition)) == 1
        assert "403" in capsys.readouterr().err
