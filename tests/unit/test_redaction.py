from __future__ import annotations

import json
import logging

import pytest

from openmcp_gateway.logconfig import JsonFormatter, RedactingFilter
from openmcp_gateway.redaction import REDACTED, Redactor, redactor


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnop",
        "authorization=Basic dXNlcjpwYXNz",
        "api_key=supersecretvalue",
        'GITHUB_TOKEN="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
        '{"password": "hunter2hunter2"}',
        "using key sk-abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_patterns_are_redacted(text: str) -> None:
    out = Redactor().redact(text)
    assert REDACTED in out
    for secret in ("abcdefghijklmnop", "dXNlcjpwYXNz", "supersecretvalue", "ghp_aaaa", "hunter2", "sk-abc"):
        assert secret not in out


def test_registered_values_are_redacted_anywhere() -> None:
    r = Redactor()
    r.register(["s3cr3t-value-123", "x"])  # too-short values are ignored
    assert r.redact("prefix s3cr3t-value-123 suffix") == f"prefix {REDACTED} suffix"
    assert r.redact("x marks") == "x marks"


def test_log_records_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    redactor.register(["registered-secret-xyz"])
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "value %s", ("registered-secret-xyz",), None)
    record.detail = "Bearer abcdefghijklmnopqrst"
    assert RedactingFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == f"value {REDACTED}"
    assert "abcdefghijklmnopqrst" not in payload["detail"]
