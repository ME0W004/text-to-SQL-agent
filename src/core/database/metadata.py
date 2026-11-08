# src/core/database/metadata.py

"""Read-only database metadata introspection for the Schema Intelligence layer.

This module is, together with ``connection.py``, the only approved location
for direct database-driver imports used for schema discovery
(``docs/architecture/schema-intelligence.md`` §3, ADR-017). It is an
introspection adapter, NOT a second execution path:

* every statement is a module-level constant — no user-controlled
  identifier or SQL ever reaches this layer;
* connections are opened only while a read is in flight and always
  closed on every exit path;
* output is driver-free typed value objects (``DatabaseMetadata``) —
  cursors, connections, ``pymssql`` types and driver exceptions never
  cross this boundary;
* failures map into the repository's typed database error model
  (``DatabaseError`` / ``DatabaseErrorCode``), mirroring the conventions
  of ``connection.py``.

Capability notes (documented honestly):
* tables/columns/ordinal/types/nullability via ``INFORMATION_SCHEMA.COLUMNS``;
* primary keys via ``TABLE_CONSTRAINTS`` ⋈ ``KEY_COLUMN_USAGE``;
* foreign keys via ``REFERENTIAL_CONSTRAINTS`` ⋈ ``KEY_COLUMN_USAGE``
  (composite keys emit one row per column pair, matched by ordinal);
* row statistics, indexes, defaults and comments are NOT discovered —
  absence is represented by not returning them, never fabricated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Optional, Protocol

from src.core.database.policy import DatabaseError, DatabaseErrorCode, ExecutionPolicy
from src.utils.logger import log_event

__all__ = [
    "DiscoveredColumn",
    "DiscoveredPrimaryKey",
    "DiscoveredForeignKey",
    "ProbeSignature",
    "DatabaseMetadata",
    "DatabaseMetadataReader",
]


# Schemas excluded from discovery. Everything else visible to the login
# is discovered — this is deliberately broader than the legacy hardcoded
# three-table scope. Documented limitation: an exotic deployment could
# name a business schema exactly like a system one; the list is constant
# and reviewable.
_SYSTEM_SCHEMAS: tuple[str, ...] = (
    "sys",
    "INFORMATION_SCHEMA",
    "guest",
    "db_owner",
    "db_accessadmin",
    "db_securityadmin",
    "db_ddladmin",
    "db_backupoperator",
    "db_datareader",
    "db_datawriter",
    "db_denydatareader",
    "db_denydatawriter",
)

_IN_CLAUSE = ", ".join(f"'{name}'" for name in _SYSTEM_SCHEMAS)

# Constant introspection SQL — ORDER BY is a first ordering guarantee;
# the reader re-sorts in Python so driver row order cannot matter.
_COLUMNS_SQL = f"""
    SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME,
           ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA NOT IN ({_IN_CLAUSE})
    ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
"""

_PRIMARY_KEYS_SQL = f"""
    SELECT kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.COLUMN_NAME,
           kcu.ORDINAL_POSITION
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
    JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
      ON tc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
     AND tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
     AND tc.TABLE_NAME = kcu.TABLE_NAME
    WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
      AND kcu.TABLE_SCHEMA NOT IN ({_IN_CLAUSE})
    ORDER BY kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.ORDINAL_POSITION
"""

_FOREIGN_KEYS_SQL = f"""
    SELECT rc.CONSTRAINT_SCHEMA, rc.CONSTRAINT_NAME,
           kcu1.TABLE_SCHEMA, kcu1.TABLE_NAME, kcu1.COLUMN_NAME,
           kcu1.ORDINAL_POSITION,
           kcu2.TABLE_SCHEMA, kcu2.TABLE_NAME, kcu2.COLUMN_NAME
    FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
    JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu1
      ON kcu1.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA
     AND kcu1.CONSTRAINT_NAME = rc.CONSTRAINT_NAME
    JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu2
      ON kcu2.CONSTRAINT_SCHEMA = rc.UNIQUE_CONSTRAINT_SCHEMA
     AND kcu2.CONSTRAINT_NAME = rc.UNIQUE_CONSTRAINT_NAME
     AND kcu2.ORDINAL_POSITION = kcu1.ORDINAL_POSITION
    WHERE kcu1.TABLE_SCHEMA NOT IN ({_IN_CLAUSE})
    ORDER BY rc.CONSTRAINT_SCHEMA, rc.CONSTRAINT_NAME, kcu1.ORDINAL_POSITION
"""

# Cheap change probe: one aggregate query over the same metadata the
# full discovery reads. CHECKSUM_AGG has a (remote) collision risk and
# does not cover constraint metadata — the probe is a skip optimisation
# only; the post-discovery SHA-256 fingerprint remains the identity
# authority (docs/architecture/schema-intelligence.md §9).
_PROBE_SQL = f"""
    SELECT COUNT_BIG(*), CHECKSUM_AGG(CHECKSUM(
        TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME,
        ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE))
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA NOT IN ({_IN_CLAUSE})
"""


@dataclass(frozen=True)
class DiscoveredColumn:
    """One column as discovered from database metadata."""

    table_schema: str
    table_name: str
    name: str
    ordinal_position: int
    data_type: str
    nullable: bool


@dataclass(frozen=True)
class DiscoveredPrimaryKey:
    """One primary-key column (composite PKs emit one row per column)."""

    table_schema: str
    table_name: str
    column_name: str
    key_ordinal: int


@dataclass(frozen=True)
class DiscoveredForeignKey:
    """One FK column pair (composite FKs emit one row per column pair)."""

    constraint_name: str
    from_schema: str
    from_table: str
    from_column: str
    key_ordinal: int
    to_schema: str
    to_table: str
    to_column: str


@dataclass(frozen=True)
class ProbeSignature:
    """Cheap structural probe result used to skip redundant discovery."""

    row_count: int
    checksum: Optional[int]  # None when the table set is empty


@dataclass(frozen=True)
class DatabaseMetadata:
    """Driver-free snapshot of discovered structural metadata."""

    columns: tuple[DiscoveredColumn, ...] = ()
    primary_keys: tuple[DiscoveredPrimaryKey, ...] = ()
    foreign_keys: tuple[DiscoveredForeignKey, ...] = ()


def _is_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text or "time-out" in text


def _map_error(exc: Exception) -> DatabaseError:
    """Map driver failures into the typed database error model.

    Mirrors the timeout/permission classification of
    ``DatabaseConnection._map_error`` without reusing a private method.
    """
    if isinstance(exc, DatabaseError):
        return exc
    if _is_timeout(exc):
        code: DatabaseErrorCode = DatabaseErrorCode.DATABASE_TIMEOUT
    else:
        text = str(exc).lower()
        if any(word in text for word in ("permission", "denied", "not authorized", "access is denied")):
            code = DatabaseErrorCode.DATABASE_PERMISSION_ERROR
        else:
            code = DatabaseErrorCode.DATABASE_QUERY_ERROR
    return DatabaseError(code, f"{code.value}: metadata introspection failed.", cause=exc)


class DatabaseMetadataReader:
    """Read-only structural metadata reader over a dedicated connection.

    The connection is opened per read call and closed on every exit path.
    Construction performs no I/O, so connection parameters are optional
    at construction time; incomplete credentials surface only at read
    time as a typed connection failure. A ``connect_fn`` may be injected
    for tests (the established ``DatabaseConnection`` pattern).
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        user: str = "",
        password: str = "",
        database: Optional[str] = None,
        connect_fn: Optional[Callable[..., Any]] = None,
        execution_policy: Optional[ExecutionPolicy] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        # Lazy driver import (ADR-016 lazy-provider pattern): importing
        # this module — or constructing the reader with an injected
        # ``connect_fn`` — loads no driver; only real construction with
        # the default connection path does.
        if connect_fn is not None:
            self._connect_fn: Callable[..., Any] = connect_fn
        else:
            import pymssql

            self._connect_fn = pymssql.connect
        self.execution_policy = execution_policy or ExecutionPolicy.from_environment()

    # -- internals -------------------------------------------------------

    def _connect(self) -> Any:
        try:
            return self._connect_fn(
                server=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                timeout=self.execution_policy.query_timeout,
                login_timeout=self.execution_policy.connection_timeout,
                charset="UTF-8",
            )
        except Exception as exc:
            raise _map_error(exc) from exc

    def _fetch_all(self, connection: Any, sql: str) -> list[tuple]:
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            return [tuple(row) for row in cursor.fetchall()]
        except Exception as exc:
            raise _map_error(exc) from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception as exc:
                    log_event(
                        "db_cursor_close_failed",
                        level=logging.WARNING,
                        component="database",
                        error_type=type(exc).__name__,
                    )

    # -- public read surface ---------------------------------------------

    def read_probe(self) -> ProbeSignature:
        """Run the cheap structural probe (single aggregate query)."""
        started = perf_counter()
        connection = None
        try:
            connection = self._connect()
            rows = self._fetch_all(connection, _PROBE_SQL)
        except DatabaseError:
            raise
        finally:
            if connection is not None:
                self._close(connection)
        row = rows[0] if rows else (0, None)
        signature = ProbeSignature(row_count=int(row[0]), checksum=None if row[1] is None else int(row[1]))
        log_event(
            "schema_probe_completed",
            component="schema",
            row_count=signature.row_count,
            duration_ms=round((perf_counter() - started) * 1000, 3),
        )
        return signature

    def read_metadata(self) -> DatabaseMetadata:
        """Run full discovery (one connection, three constant queries)."""
        started = perf_counter()
        connection = None
        try:
            connection = self._connect()
            column_rows = self._fetch_all(connection, _COLUMNS_SQL)
            pk_rows = self._fetch_all(connection, _PRIMARY_KEYS_SQL)
            fk_rows = self._fetch_all(connection, _FOREIGN_KEYS_SQL)
        except DatabaseError:
            raise
        finally:
            if connection is not None:
                self._close(connection)

        columns = tuple(sorted(
            {
                DiscoveredColumn(
                    table_schema=str(r[0]),
                    table_name=str(r[1]),
                    name=str(r[2]),
                    ordinal_position=int(r[3]),
                    data_type=str(r[4]),
                    nullable=str(r[5]).upper() == "YES",
                )
                for r in column_rows
            },
            key=lambda c: (c.table_schema, c.table_name, c.ordinal_position, c.name),
        ))
        primary_keys = tuple(sorted(
            {
                DiscoveredPrimaryKey(
                    table_schema=str(r[0]),
                    table_name=str(r[1]),
                    column_name=str(r[2]),
                    key_ordinal=int(r[3]),
                )
                for r in pk_rows
            },
            key=lambda p: (p.table_schema, p.table_name, p.key_ordinal, p.column_name),
        ))
        foreign_keys = tuple(sorted(
            {
                DiscoveredForeignKey(
                    constraint_name=str(r[1]),
                    from_schema=str(r[2]),
                    from_table=str(r[3]),
                    from_column=str(r[4]),
                    key_ordinal=int(r[5]),
                    to_schema=str(r[6]),
                    to_table=str(r[7]),
                    to_column=str(r[8]),
                )
                for r in fk_rows
            },
            key=lambda f: (
                f.from_schema, f.from_table, f.constraint_name,
                f.key_ordinal, f.from_column, f.to_table, f.to_column,
            ),
        ))
        log_event(
            "schema_discovery_completed",
            component="schema",
            table_count=len({(c.table_schema, c.table_name) for c in columns}),
            column_count=len(columns),
            primary_key_count=len(primary_keys),
            foreign_key_count=len(foreign_keys),
            duration_ms=round((perf_counter() - started) * 1000, 3),
        )
        return DatabaseMetadata(columns=columns, primary_keys=primary_keys, foreign_keys=foreign_keys)

    @staticmethod
    def _close(connection: Any) -> None:
        try:
            connection.close()
        except Exception as exc:
            log_event(
                "db_connection_close_failed",
                level=logging.WARNING,
                component="database",
                error_type=type(exc).__name__,
            )
