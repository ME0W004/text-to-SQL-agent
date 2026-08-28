# src/core/database/connection.py

"""Controlled, isolated database execution for SQL Server.

Security validation remains the first gate.  This module owns only resource
limits, driver interaction, error translation, and cleanup.
"""

from __future__ import annotations

import logging
import os
from time import perf_counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

import pymssql

from src.core.security.sql_security import SQLSecurityValidator, SQLSecurityError, SecurityReasonCode
from src.utils.exceptions import QueryExecutionError
from src.utils.logger import log_event, sql_fingerprint

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


class DatabaseConnection:
    def __init__(self, host, port, user, password, database=None,
                 execution_policy: ExecutionPolicy | None = None,
                 connect_fn: Callable[..., Any] | None = None):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection = None
        self.cursor = None
        self.security_validator = SQLSecurityValidator()
        self.execution_policy = execution_policy or ExecutionPolicy.from_environment()
        self._connect_fn = connect_fn or pymssql.connect

    def connect(self, database=None):
        db = database or self.database
        connection = None
        try:
            connection = self._connect_fn(
                server=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=db,
                timeout=self.execution_policy.query_timeout,
                login_timeout=self.execution_policy.connection_timeout,
                charset='UTF-8'
            )
            self.connection = connection
            log_event("db_connection_opened", component="database")
            return self.connection
        except Exception as e:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            self.connection = None
            self.cursor = None
            default = DatabaseErrorCode.DATABASE_CONNECTION_ERROR
            mapped = self._map_error(e, default)
            log_event("db_connection_failed", level=logging.ERROR, component="database",
                      error_code=mapped.code.value, error_type=type(e).__name__)
            raise mapped from e

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            except Exception as exc:
                log_event("db_connection_close_failed", level=logging.WARNING, component="database",
                          error_type=type(exc).__name__)
            finally:
                self.connection = None
                self.cursor = None
            log_event("db_connection_closed", component="database")

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        text = str(exc).lower()
        return "timeout" in text or "timed out" in text or "time-out" in text

    @classmethod
    def _map_error(cls, exc: Exception, default: DatabaseErrorCode,
                   *, sql: str | None = None) -> DatabaseError:
        if isinstance(exc, DatabaseError):
            return exc
        if cls._is_timeout(exc):
            code = DatabaseErrorCode.DATABASE_TIMEOUT
        else:
            text = str(exc).lower()
            if any(word in text for word in ("permission", "denied", "not authorized", "access is denied")):
                code = DatabaseErrorCode.DATABASE_PERMISSION_ERROR
            elif isinstance(exc, (pymssql.InterfaceError, pymssql.OperationalError)):
                code = default
            else:
                code = default
        return DatabaseError(code, f"{code.value}: database operation failed.", sql=sql, cause=exc)

    def execute_query(self, query, params=None):
        log_event("sql_validation_started", component="security", sql_fingerprint=sql_fingerprint(query))
        try:
            decision = self.security_validator.validate(query)
        except Exception as exc:
            log_event("sql_validation_failed", level=logging.ERROR, component="security",
                      reason_code="SECURITY_VALIDATION_FAILURE",
                      error_type=type(exc).__name__, sql_fingerprint=sql_fingerprint(query))
            raise SQLSecurityError(
                SecurityReasonCode.SQL_PARSE_ERROR,
                "Security validation failed closed.",
                sql=query,
            ) from exc
        if not decision.valid:
            code = decision.reason_code.value if decision.reason_code else "SECURITY_REJECTED"
            log_event("sql_validation_failed", level=logging.WARNING, component="security",
                      reason_code=code, sql_fingerprint=sql_fingerprint(query))
            raise SQLSecurityError(decision.reason_code, f"{code}: {decision.message}", sql=query)
        log_event("sql_validation_passed", component="security", sql_fingerprint=sql_fingerprint(query))
        # Never use the legacy shared cursor for request execution.  A fresh
        # connection/cursor pair prevents cross-request state contamination.
        connection = cursor = None
        started = perf_counter()
        log_event("db_execution_started", component="database", sql_fingerprint=sql_fingerprint(query))
        try:
            try:
                connection = self._connect_fn(
                    server=self.host, port=self.port, user=self.user,
                    password=self.password, database=self.database,
                    timeout=self.execution_policy.query_timeout,
                    login_timeout=self.execution_policy.connection_timeout,
                    charset="UTF-8", as_dict=True,
                )
            except Exception as exc:
                default = (DatabaseErrorCode.DATABASE_CONNECTION_ERROR
                           if isinstance(exc, (pymssql.InterfaceError, pymssql.OperationalError))
                           else DatabaseErrorCode.DATABASE_UNKNOWN_ERROR)
                raise self._map_error(exc, default, sql=query) from exc
            try:
                cursor = connection.cursor(as_dict=True)
            except Exception as exc:
                raise self._map_error(exc, DatabaseErrorCode.DATABASE_CURSOR_ERROR, sql=query) from exc
            try:
                if hasattr(cursor, "arraysize"):
                    cursor.arraysize = self.execution_policy.max_fetch_batch
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
            except Exception as exc:
                raise self._map_error(exc, DatabaseErrorCode.DATABASE_QUERY_ERROR, sql=query) from exc

            rows: list[dict] = []
            batch_size = self.execution_policy.max_fetch_batch
            try:
                while len(rows) <= self.execution_policy.max_rows:
                    remaining = self.execution_policy.max_rows + 1 - len(rows)
                    batch = cursor.fetchmany(min(batch_size, remaining))
                    if not batch:
                        break
                    # Defensive cap even if a non-conforming cursor ignores
                    # the fetchmany(size) argument.
                    rows.extend(batch[:remaining])
            except Exception as exc:
                raise self._map_error(exc, DatabaseErrorCode.DATABASE_CURSOR_ERROR, sql=query) from exc
            if len(rows) > self.execution_policy.max_rows:
                log_event("db_execution_failed", level=logging.ERROR, component="database",
                          error_code=DatabaseErrorCode.DATABASE_RESULT_LIMIT.value,
                          sql_fingerprint=sql_fingerprint(query),
                          duration_ms=round((perf_counter() - started) * 1000, 3))
                raise DatabaseError(
                    DatabaseErrorCode.DATABASE_RESULT_LIMIT,
                    f"{DatabaseErrorCode.DATABASE_RESULT_LIMIT.value}: result exceeds max_rows={self.execution_policy.max_rows}.",
                    sql=query,
                )
            log_event("db_execution_completed", component="database", row_count=len(rows),
                      sql_fingerprint=sql_fingerprint(query),
                      duration_ms=round((perf_counter() - started) * 1000, 3))
            return rows
        except DatabaseError as exc:
            if exc.code != DatabaseErrorCode.DATABASE_RESULT_LIMIT:
                event = "db_timeout" if exc.code == DatabaseErrorCode.DATABASE_TIMEOUT else "db_execution_failed"
                log_event(event, level=logging.ERROR, component="database",
                          error_code=exc.code.value, sql_fingerprint=sql_fingerprint(query),
                          duration_ms=round((perf_counter() - started) * 1000, 3))
            raise
        except Exception as exc:
            mapped = self._map_error(exc, DatabaseErrorCode.DATABASE_UNKNOWN_ERROR, sql=query)
            log_event("db_execution_failed", level=logging.ERROR, component="database",
                      error_code=mapped.code.value, sql_fingerprint=sql_fingerprint(query),
                      duration_ms=round((perf_counter() - started) * 1000, 3))
            raise mapped from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception as exc:
                    log_event("db_cursor_close_failed", level=logging.WARNING, component="database",
                              error_type=type(exc).__name__)
            if connection is not None:
                try:
                    connection.close()
                except Exception as exc:
                    log_event("db_connection_close_failed", level=logging.WARNING, component="database",
                              error_type=type(exc).__name__)

    def execute_non_query(self, query, params=None):
        raise SQLSecurityError(
            code=SecurityReasonCode.NON_READ_ONLY_STATEMENT,
            message="Non-query execution is disabled by the read-only SQL security policy.",
            sql=query,
        )
