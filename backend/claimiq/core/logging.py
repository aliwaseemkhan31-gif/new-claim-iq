"""Structured logging.

The requirement is no scattered ``print()`` calls and logs that identify the
request, user, project, operation and job. The legacy prototype logged with
``print()`` (`../backend/rag.py:296`, `../backend/contract_parser.py:94`), which
produces output that cannot be filtered, correlated or shipped anywhere.

Context is carried in a :class:`contextvars.ContextVar` so it survives across
async boundaries and is not shared between concurrent requests.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

#: Per-request/per-task context. A ContextVar rather than thread-local because
#: ASGI handlers and Celery's threaded pools both break thread-locals.
_context: ContextVar[dict[str, Any]] = ContextVar("claimiq_log_context", default={})

#: Attributes present on every LogRecord that are not part of the payload.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


def get_context() -> dict[str, Any]:
    return dict(_context.get())


def bind_context(**values: Any) -> None:
    """Add values to the current logging context.

    ``None`` values are dropped so a caller can pass optional identifiers
    without guarding each one.
    """
    current = dict(_context.get())
    current.update({k: v for k, v in values.items() if v is not None})
    _context.set(current)


def clear_context() -> None:
    _context.set({})


class RequestContextFilter(logging.Filter):
    """Merge the ambient context onto each record.

    ``request_id`` is always set, so the console formatter can reference it
    without a KeyError on log lines emitted outside a request.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        context = get_context()
        for key, value in context.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        if not hasattr(record, "request_id"):
            record.request_id = context.get("request_id", "-")
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line.

    Chosen over a logging library so an air-gapped deployment carries one fewer
    dependency, and because the output shape is small enough to own.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = _safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def _safe(value: Any) -> Any:
    """Coerce a value to something JSON can render."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. Always use this rather than ``print``."""
    return logging.getLogger(name if name.startswith("claimiq") else f"claimiq.{name}")
