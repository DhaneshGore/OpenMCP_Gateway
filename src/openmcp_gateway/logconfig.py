"""Structured logging with redaction and request correlation."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any

from openmcp_gateway.redaction import redactor

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "color_message",
}


class RedactingFilter(logging.Filter):
    """Redacts the rendered message and string extras of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed %-format args; keep the raw template
            message = str(record.msg)
        record.msg = redactor.redact(message)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED and isinstance(value, str):
                setattr(record, key, redactor.redact(value))
        if record.exc_text:
            record.exc_text = redactor.redact(record.exc_text)
        rid = request_id_var.get()
        if rid is not None and not hasattr(record, "request_id"):
            record.request_id = rid
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = redactor.redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        if record.exc_text:
            base = redactor.redact(base)
        return base


def configure_logging(level: str = "info", fmt: str = "json") -> None:
    """Install a single redacting handler on the root logger (idempotent)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_openmcp", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._openmcp = True  # type: ignore[attr-defined]
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn installs its own handlers when log_config is set; we pass
    # log_config=None and let its loggers propagate here instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
