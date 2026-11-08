# tests/test_schema/test_metadata_adapter.py

"""Database metadata adapter tests (Sprint 3.1B Phase 24).

All tests run against an injected fake ``connect_fn`` — no live database.
Synthetic PK/FK rows exist purely to exercise discovery paths; the
checked-in evidence fixture (``fixtures/three_table_metadata.json``)
remains the pure documented schema.
"""

from __future__ import annotations

import pytest

from src.core.database.metadata import (
    DatabaseMetadata,
    DatabaseMetadataReader,
    ProbeSignature,
    _COLUMNS_SQL,
    _FOREIGN_KEYS_SQL,
    _PRIMARY_KEYS_SQL,
    _PROBE_SQL,
)
from src.core.database.policy import DatabaseErrorCode


class FakeCursor:
    """Serves constant row batches per statement family."""

    def __init__(self, results: dict[str, list[tuple]]) -> None:
        self._results = results
        self.executed: list[str] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if params:
            raise AssertionError("introspection statements must never be parameterized")

    def fetchall(self):
        last = self.executed[-1]
        matches = [fragment for fragment in self._results if fragment in last]
        if not matches:
            raise AssertionError(f"no fake result configured for statement: {last[:60]}...")
        # Longest fragment wins: e.g. the probe statement contains both
        # "FROM INFORMATION_SCHEMA.COLUMNS" and "CHECKSUM_AGG".
        return self._results[max(matches, key=len)]

    def close(self):
        pass


class FakeConnection:
    def __init__(self, results: dict[str, list[tuple]]) -> None:
        self.cursor_obj = FakeCursor(results)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def make_reader(results, *, closed_flag: list | None = None):
    connection = FakeConnection(results)
    if closed_flag is not None:
        original_close = connection.close

        def close():
            closed_flag.append(True)
            original_close()

        connection.close = close
    reader = DatabaseMetadataReader(
        host="h", port=1, user="u", password="p", connect_fn=lambda **kwargs: connection
    )
    return reader, connection


# Unique substrings per statement family (the probe statement also
# contains "FROM INFORMATION_SCHEMA.COLUMNS", so the columns marker is
# its ORDER BY clause, which the aggregate probe does not have).
COLUMNS_FRAGMENT = "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"


class TestColumnDiscovery:
    def test_discovery_normalizes_and_orders_rows(self):
        # Rows deliberately shuffled; DATA_TYPE/IS_NULLABLE as the driver
        # returns them.
        results = {
            COLUMNS_FRAGMENT: [
                ("Sale", "Orders", "status", 5, "nvarchar", "NO"),
                ("Sale", "Orders", "Id", 1, "int", "NO"),
                ("Sale", "Orders", "entityDate", 3, "date", "YES"),
                ("Sale", "Orders", "serialNo", 4, "bigint", "YES"),
            ],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(4, 987)],
        }
        reader, connection = make_reader(results)
        metadata = reader.read_metadata()
        assert [c.name for c in metadata.columns] == ["Id", "entityDate", "serialNo", "status"]
        assert metadata.columns[0].data_type == "int"
        assert metadata.columns[0].nullable is False
        assert metadata.columns[1].nullable is True
        assert connection.closed is True

    def test_probe_signature(self):
        results = {
            COLUMNS_FRAGMENT: [],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(42, 7)],
        }
        reader, _ = make_reader(results)
        assert reader.read_probe() == ProbeSignature(row_count=42, checksum=7)

    def test_probe_handles_null_checksum(self):
        results = {
            COLUMNS_FRAGMENT: [],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(0, None)],
        }
        reader, _ = make_reader(results)
        assert reader.read_probe() == ProbeSignature(row_count=0, checksum=None)

    def test_statements_are_constant_and_never_parameterized(self):
        results = {
            COLUMNS_FRAGMENT: [],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(0, None)],
        }
        reader, connection = make_reader(results)
        reader.read_probe()
        reader.read_metadata()
        assert set(connection.cursor_obj.executed) == {
            _PROBE_SQL, _COLUMNS_SQL, _PRIMARY_KEYS_SQL, _FOREIGN_KEYS_SQL,
        }

    def test_duplicate_rows_are_deduplicated(self):
        dup = ("Sale", "Orders", "Id", 1, "int", "NO")
        results = {
            COLUMNS_FRAGMENT: [dup, dup, ("Sale", "Orders", "status", 5, "nvarchar", "NO")],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(2, 5)],
        }
        reader, _ = make_reader(results)
        metadata = reader.read_metadata()
        assert [c.name for c in metadata.columns] == ["Id", "status"]


class TestKeyDiscovery:
    def test_primary_key_discovery(self):
        results = {
            COLUMNS_FRAGMENT: [
                ("dbo", "T", "Id", 1, "int", "NO"),
                ("dbo", "T", "name", 2, "nvarchar", "YES"),
            ],
            "PRIMARY KEY": [("dbo", "T", "Id", 1)],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(2, 5)],
        }
        reader, _ = make_reader(results)
        metadata = reader.read_metadata()
        assert len(metadata.primary_keys) == 1
        assert metadata.primary_keys[0].column_name == "Id"

    def test_foreign_key_discovery_including_composite(self):
        results = {
            COLUMNS_FRAGMENT: [("dbo", "C", "a", 1, "int", "YES")],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [
                ("dbo", "FK_C", "dbo", "C", "a", 1, "dbo", "P", "pa"),
                ("dbo", "FK_C", "dbo", "C", "b", 2, "dbo", "P", "pb"),
            ],
            "CHECKSUM_AGG": [(1, 5)],
        }
        reader, _ = make_reader(results)
        metadata = reader.read_metadata()
        assert len(metadata.foreign_keys) == 2
        assert metadata.foreign_keys[0].constraint_name == "FK_C"
        assert (metadata.foreign_keys[0].from_column, metadata.foreign_keys[0].to_column) == ("a", "pa")
        assert (metadata.foreign_keys[1].from_column, metadata.foreign_keys[1].to_column) == ("b", "pb")


class TestEmptyAndOrdering:
    def test_empty_metadata(self):
        results = {
            COLUMNS_FRAGMENT: [],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(0, None)],
        }
        reader, _ = make_reader(results)
        metadata = reader.read_metadata()
        assert metadata == DatabaseMetadata()

    def test_metadata_ordering_is_row_order_independent(self):
        base = [
            ("Sale", "Orders", "Id", 1, "int", "NO"),
            ("Sale", "Orders", "status", 5, "nvarchar", "NO"),
            ("Sale", "OrderItems", "Id", 1, "int", "NO"),
        ]
        results_a = {COLUMNS_FRAGMENT: base, "PRIMARY KEY": [], "REFERENTIAL_CONSTRAINTS": [], "CHECKSUM_AGG": [(3, 1)]}
        results_b = {COLUMNS_FRAGMENT: list(reversed(base)), "PRIMARY KEY": [], "REFERENTIAL_CONSTRAINTS": [], "CHECKSUM_AGG": [(3, 1)]}
        reader_a, _ = make_reader(results_a)
        reader_b, _ = make_reader(results_b)
        assert reader_a.read_metadata() == reader_b.read_metadata()


class TestFailureMappingAndCleanup:
    def test_timeout_failure_maps_to_database_timeout(self):
        def bad_connect(**kwargs):
            raise RuntimeError("Query timeout expired")

        reader = DatabaseMetadataReader(host="h", port=1, user="u", password="p", connect_fn=bad_connect)
        with pytest.raises(Exception) as excinfo:
            reader.read_metadata()
        assert excinfo.value.code == DatabaseErrorCode.DATABASE_TIMEOUT

    def test_permission_failure_maps_to_permission_error(self):
        def bad_connect(**kwargs):
            raise RuntimeError("The SELECT permission was denied")

        reader = DatabaseMetadataReader(host="h", port=1, user="u", password="p", connect_fn=bad_connect)
        with pytest.raises(Exception) as excinfo:
            reader.read_probe()
        assert excinfo.value.code == DatabaseErrorCode.DATABASE_PERMISSION_ERROR

    def test_generic_failure_maps_to_query_error(self):
        def bad_connect(**kwargs):
            raise RuntimeError("connection reset")

        reader = DatabaseMetadataReader(host="h", port=1, user="u", password="p", connect_fn=bad_connect)
        with pytest.raises(Exception) as excinfo:
            reader.read_metadata()
        assert excinfo.value.code == DatabaseErrorCode.DATABASE_QUERY_ERROR

    def test_connection_closed_on_success_and_failure(self):
        closed = []
        results = {
            COLUMNS_FRAGMENT: [],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(0, None)],
        }
        reader, connection = make_reader(results, closed_flag=closed)
        reader.read_metadata()
        assert closed == [True]

        def failing_cursor_execute(sql, params=None):
            raise RuntimeError("query failed mid-flight")

        results2 = {
            COLUMNS_FRAGMENT: [],
            "PRIMARY KEY": [],
            "REFERENTIAL_CONSTRAINTS": [],
            "CHECKSUM_AGG": [(0, None)],
        }
        connection2 = FakeConnection(results2)
        connection2.cursor_obj.execute = failing_cursor_execute
        reader2 = DatabaseMetadataReader(
            host="h", port=1, user="u", password="p", connect_fn=lambda **kwargs: connection2
        )
        with pytest.raises(Exception):
            reader2.read_metadata()
        assert connection2.closed is True
