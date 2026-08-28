"""Central structured logging, trace context, and sensitive-data redaction."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Mapping

TRACE_ID_HEADER = "X-Trace-ID"
_trace_context: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "trace_context", default=None
)
_configured = False
_SENSITIVE_KEYS = re.compile(
    r"(?:password|passwd|api[_-]?key|token|secret|authorization|cookie|connection[_-]?string|credential)",
    re.IGNORECASE,
)
_PRIVATE_PAYLOAD_KEYS = {"sql", "question", "answer", "prompt", "response", "rows", "results"}
_SAFE_TRACE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SECRET_TEXT = re.compile(
    r"(?i)(password|passwd|api[_-]?key|token|secret|authorization)\s*[:=]\s*([^\s,;]+)"
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    session_id: str | None = None


def new_trace_id() -> str:
    return uuid.uuid4().hex


def normalize_trace_id(trace_id: str | None) -> str:
    return trace_id if trace_id and _SAFE_TRACE_ID.fullmatch(trace_id) else new_trace_id()


def get_trace_context() -> TraceContext | None:
    return _trace_context.get()


def set_trace_context(trace_id: str | None = None, session_id: str | None = None):
    """Set request-local context and return a token suitable for reset()."""
    return _trace_context.set(TraceContext(normalize_trace_id(trace_id), session_id))


def reset_trace_context(token) -> None:
    _trace_context.reset(token)


def update_session_id(session_id: str | None) -> None:
    context = get_trace_context()
    if context is not None:
        _trace_context.set(TraceContext(context.trace_id, session_id))


def sql_fingerprint(sql: str | None) -> str | None:
    if not sql:
        return None
    normalized = re.sub(r"\s+", " ", sql).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secret-like fields and make values JSON-safe."""
    if key and _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if key and key.lower() in _PRIVATE_PAYLOAD_KEYS:
        return "[PAYLOAD_REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return "[BYTES_REDACTED]"
    if isinstance(value, str):
        return _SECRET_TEXT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = get_trace_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", "log"),
            "component": getattr(record, "component", record.name),
            "message": sanitize(record.getMessage()),
        }
        if context:
            payload["trace_id"] = context.trace_id
            if context.session_id:
                payload["session_id"] = context.session_id
        ignored = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName", "processName",
            "process", "message", "event", "component", "taskName",
        }
        for key, value in record.__dict__.items():
            if not key.startswith("_") and key not in ignored:
                payload[key] = sanitize(value, key=key)
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(sanitize(payload), ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    formatter = JsonFormatter()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(formatter)
    else:
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(formatter)
        console._text_to_sql_console = True
        root.addHandler(console)
    has_rotating_file = any(
        isinstance(handler, RotatingFileHandler) and getattr(handler, "_text_to_sql_file", False)
        for handler in root.handlers
    )
    if not has_rotating_file:
        log_dir = os.getenv("LOG_DIR", "logs")
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"), maxBytes=10 * 1024 * 1024,
            backupCount=5, encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler._text_to_sql_file = True
        root.addHandler(file_handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_event(event: str, *, level: int = logging.INFO, component: str | None = None,
              logger: logging.Logger | None = None, **fields: Any) -> None:
    target = logger or get_logger("app")
    safe_fields = {key: sanitize(value, key=key) for key, value in fields.items()}
    target.log(level, safe_fields.pop("message", ""), extra={
        "event": event,
        "component": component or target.name,
        **safe_fields,
    })


__all__ = [
    "TRACE_ID_HEADER", "TraceContext", "new_trace_id", "normalize_trace_id", "get_trace_context",
    "set_trace_context", "reset_trace_context", "update_session_id", "sql_fingerprint",
    "sanitize", "configure_logging", "get_logger", "log_event", "JsonFormatter",
]
