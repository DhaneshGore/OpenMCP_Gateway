from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from openmcp_gateway.config import GatewayConfig, StdioServerConfig

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_server.py"


def fake_server_config(server_id: str = "fake", mode: str = "normal", **overrides: Any) -> StdioServerConfig:
    data: dict[str, Any] = {
        "id": server_id,
        "command": sys.executable,
        "args": [str(FAKE_SERVER), mode],
        "timeouts": {"startup": 5, "request": 5, "shutdown": 2},
    }
    data.update(overrides)
    return StdioServerConfig(**data)


def demo_server_config(server_id: str = "demo", **overrides: Any) -> StdioServerConfig:
    data: dict[str, Any] = {
        "id": server_id,
        "command": sys.executable,
        "args": ["-m", "openmcp_gateway.demo.echo_server"],
        "timeouts": {"startup": 15, "request": 10, "shutdown": 2},
    }
    data.update(overrides)
    return StdioServerConfig(**data)


def gateway_config(servers: list[StdioServerConfig], **sections: Any) -> GatewayConfig:
    return GatewayConfig(servers=servers, **sections)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep developer/CI OPENMCP_* variables from leaking into config tests.
    import os

    for key in list(os.environ):
        if key.startswith("OPENMCP_"):
            monkeypatch.delenv(key)


@pytest.fixture(scope="session")
def s3_endpoint() -> Iterator[str]:
    """An in-process S3-compatible server (moto) reachable over real HTTP."""
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


def make_s3(endpoint: str, bucket: str) -> Any:
    import boto3

    from openmcp_gateway.packages.artifacts import S3ArtifactStore

    boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ).create_bucket(Bucket=bucket)
    return S3ArtifactStore(
        bucket=bucket,
        prefix="packages/",
        endpoint_url=endpoint,
        region="us-east-1",
        access_key_id="testing",
        secret_access_key="testing",
    )


# Read at import time: the autouse fixture above clears OPENMCP_* variables per test.
MONGO_URI = os.environ.get("OPENMCP_TEST_MONGODB_URI")
REQUIRE_LANDLOCK = os.environ.get("OPENMCP_REQUIRE_LANDLOCK") == "1"


def require_landlock(minimum: int = 1, what: str = "Linux Landlock") -> None:
    """Skip a security test when the kernel lacks Landlock, unless OPENMCP_REQUIRE_LANDLOCK=1
    (set in Linux CI): then the test FAILS, so isolation is never skipped silently."""
    from openmcp_gateway.packages.bootstrap import landlock_abi

    abi = landlock_abi()
    if abi >= minimum:
        return
    message = f"{what} (Landlock ABI >= {minimum}) is not available on this kernel (ABI {abi})"
    if REQUIRE_LANDLOCK:
        pytest.fail(message + " but OPENMCP_REQUIRE_LANDLOCK=1", pytrace=False)
    pytest.skip(message)
