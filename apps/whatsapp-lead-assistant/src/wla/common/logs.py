"""Logs estruturados em JSON compatíveis com o Cloud Logging, com redação de PII."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from wla.common.security import redact_pii

trace_context: ContextVar[str | None] = ContextVar("trace_context", default=None)

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}
_SENSITIVE_KEYS = {"phone", "wa_id", "telefone", "text", "body", "token", "secret", "authorization"}


class CloudJsonFormatter(logging.Formatter):
    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": redact_pii(record.getMessage()),
            "logger": record.name,
            "time": datetime.now(UTC).isoformat(),
        }
        for key, value in vars(record).items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = "[redacted]" if key.lower() in _SENSITIVE_KEYS else value
        trace = trace_context.get()
        if trace:
            payload["logging.googleapis.com/trace"] = f"projects/{self.project}/traces/{trace}"
        if record.exc_info:
            payload["exception"] = redact_pii(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(project: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudJsonFormatter(project))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def set_trace_from_header(header: str | None) -> None:
    """Extrai o trace id do cabeçalho X-Cloud-Trace-Context (TRACE_ID/SPAN;o=1)."""
    trace_context.set(header.split("/", 1)[0] if header else None)
