# src/core/database/policy.py

"""Canonical, driver-free definitions for database policy and error types.

Sprint 2 Prompt 3B (ADR-016) moves the value objects that other layers
import — ``ExecutionPolicy``, ``DatabaseError`` and ``DatabaseErrorCode`` —
out of ``connection.py`` so that modules like the contract authority
(``src/agents/contracts.py``) can depend on them without transitively
importing the ``pymssql`` driver.

``connection.py`` re-exports these names for backwards compatibility; the
definitions below are the single canonical ones. This module must stay
free of database-driver, provider-SDK and API/UI imports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from src.utils.exceptions import QueryExecutionError

__all__ = [
    "DatabaseErrorCode",
    "DatabaseError",
    "ExecutionPolicy",
]


class DatabaseErrorCode(str, Enum):
    DATABASE_CONNECTION_ERROR = "DATABASE_CONNECTION_ERROR"
    DATABASE_TIMEOUT = "DATABASE_TIMEOUT"
    DATABASE_QUERY_ERROR = "DATABASE_QUERY_ERROR"
    DATABASE_PERMISSION_ERROR = "DATABASE_PERMISSION_ERROR"
    DATABASE_RESOURCE_LIMIT = "DATABASE_RESOURCE_LIMIT"
    DATABASE_RESULT_LIMIT = "DATABASE_RESULT_LIMIT"
    DATABASE_CURSOR_ERROR = "DATABASE_CURSOR_ERROR"
    DATABASE_UNKNOWN_ERROR = "DATABASE_UNKNOWN_ERROR"


class DatabaseError(QueryExecutionError):
    """Stable application-facing database error; driver exceptions stay private."""

    def __init__(self, code: DatabaseErrorCode, message: str, *, sql: str | None = None,
                 cause: Exception | None = None):
        self.code = code
        self.message = message
        self.sql = sql
        self.cause = cause
        super().__init__(message)


@dataclass(frozen=True)
class ExecutionPolicy:
    """Validated resource limits for one database operation."""

    max_rows: int = 1000
    query_timeout: int = 30
    connection_timeout: int = 5
    max_fetch_batch: int = 100

    def __post_init__(self) -> None:
        for name in ("max_rows", "query_timeout", "connection_timeout", "max_fetch_batch"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_environment(cls) -> "ExecutionPolicy":
        values: dict[str, int] = {}
        names = {
            "max_rows": ("DB_MAX_ROWS", cls.max_rows),
            "query_timeout": ("DB_QUERY_TIMEOUT", cls.query_timeout),
            "connection_timeout": ("DB_CONNECTION_TIMEOUT", cls.connection_timeout),
            "max_fetch_batch": ("DB_MAX_FETCH_BATCH", cls.max_fetch_batch),
        }
        for field, (env_name, default) in names.items():
            raw = os.getenv(env_name)
            if raw is None or raw == "":
                values[field] = default
                continue
            try:
                values[field] = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{env_name} must be a positive integer") from exc
        return cls(**values)
