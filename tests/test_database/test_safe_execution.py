from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pymssql
import pytest

from src.core.database.connection import (
    DatabaseConnection,
    DatabaseError,
    DatabaseErrorCode,
    ExecutionPolicy,
)
from src.core.security.sql_security import SQLSecurityError


class FakeCursor:
    def __init__(self, rows=(), execute_error=None, fetch_error=None):
        self.rows = list(rows)
        self.execute_error = execute_error
        self.fetch_error = fetch_error
        self.fetch_sizes = []
        self.executed = []
        self.closed = False
        self.arraysize = 1

    def execute(self, query, params=None):
        self.executed.append((query, params))
        if self.execute_error:
            raise self.execute_error

    def fetchmany(self, size):
        self.fetch_sizes.append(size)
        if self.fetch_error:
            raise self.fetch_error
        batch, self.rows = self.rows[:size], self.rows[size:]
        return batch

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor=None, cursor_error=None):
        self.fake_cursor = cursor or FakeCursor()
        self.cursor_error = cursor_error
        self.closed = False

    def cursor(self, as_dict=False):
        if self.cursor_error:
            raise self.cursor_error
        return self.fake_cursor

    def close(self):
        self.closed = True


class ConnectionFactory:
    def __init__(self, connections=(), error=None):
        self.connections = list(connections)
        self.error = error
        self.calls = []
        self.lock = Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            if self.error:
                raise self.error
            return self.connections.pop(0)


def make_db(factory, *, max_rows=3, batch=2):
    return DatabaseConnection(
        "host", 1433, "user", "password", "database",
        execution_policy=ExecutionPolicy(
            max_rows=max_rows,
            query_timeout=7,
            connection_timeout=4,
            max_fetch_batch=batch,
        ),
        connect_fn=factory,
    )


@pytest.mark.parametrize("rows", [[], [{"id": 1}], [{"id": 1}, {"id": 2}, {"id": 3}]])
def test_safe_results_up_to_limit_succeed_and_cleanup(rows):
    cursor = FakeCursor(rows)
    connection = FakeConnection(cursor)
    factory = ConnectionFactory([connection])

    assert make_db(factory).execute_query("SELECT id FROM items") == rows
    assert cursor.closed is True
    assert connection.closed is True
    assert all(size <= 2 for size in cursor.fetch_sizes)
    assert factory.calls[0]["timeout"] == 7
    assert factory.calls[0]["login_timeout"] == 4


def test_result_above_limit_fails_after_fetching_only_limit_plus_one():
    cursor = FakeCursor([{"id": value} for value in range(10000)])
    connection = FakeConnection(cursor)

    with pytest.raises(DatabaseError) as exc_info:
        make_db(ConnectionFactory([connection])).execute_query("SELECT id FROM items")

    assert exc_info.value.code == DatabaseErrorCode.DATABASE_RESULT_LIMIT
    assert sum(cursor.fetch_sizes) == 4
    assert len(cursor.rows) == 9996
    assert cursor.closed and connection.closed


def test_security_rejection_happens_before_connect():
    factory = ConnectionFactory([])
    db = make_db(factory)

    with pytest.raises(SQLSecurityError):
        db.execute_query("SELECT 1; DROP TABLE x")

    assert factory.calls == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (pymssql.OperationalError("connection refused"), DatabaseErrorCode.DATABASE_CONNECTION_ERROR),
        (pymssql.OperationalError("login timeout expired"), DatabaseErrorCode.DATABASE_TIMEOUT),
        (RuntimeError("unexpected"), DatabaseErrorCode.DATABASE_UNKNOWN_ERROR),
    ],
)
def test_connection_errors_are_typed(error, expected):
    with pytest.raises(DatabaseError) as exc_info:
        make_db(ConnectionFactory(error=error)).execute_query("SELECT 1")
    assert exc_info.value.code == expected
    assert exc_info.value.cause is error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (pymssql.OperationalError("query timeout expired"), DatabaseErrorCode.DATABASE_TIMEOUT),
        (pymssql.ProgrammingError("permission denied"), DatabaseErrorCode.DATABASE_PERMISSION_ERROR),
        (pymssql.ProgrammingError("invalid column"), DatabaseErrorCode.DATABASE_QUERY_ERROR),
    ],
)
def test_execute_errors_are_typed_and_cleanup(error, expected):
    cursor = FakeCursor(execute_error=error)
    connection = FakeConnection(cursor)
    with pytest.raises(DatabaseError) as exc_info:
        make_db(ConnectionFactory([connection])).execute_query("SELECT id FROM items")
    assert exc_info.value.code == expected
    assert cursor.closed and connection.closed


def test_cursor_creation_and_fetch_errors_are_typed_and_cleanup():
    creation_connection = FakeConnection(cursor_error=RuntimeError("cursor unavailable"))
    with pytest.raises(DatabaseError) as creation_error:
        make_db(ConnectionFactory([creation_connection])).execute_query("SELECT 1")
    assert creation_error.value.code == DatabaseErrorCode.DATABASE_CURSOR_ERROR
    assert creation_connection.closed

    cursor = FakeCursor(fetch_error=RuntimeError("fetch failed"))
    fetch_connection = FakeConnection(cursor)
    with pytest.raises(DatabaseError) as fetch_error:
        make_db(ConnectionFactory([fetch_connection])).execute_query("SELECT 1")
    assert fetch_error.value.code == DatabaseErrorCode.DATABASE_CURSOR_ERROR
    assert cursor.closed and fetch_connection.closed


def test_concurrent_queries_use_isolated_connections_and_cursors():
    cursors = [FakeCursor([{"query": "a"}]), FakeCursor([{"query": "b"}])]
    connections = [FakeConnection(cursor) for cursor in cursors]
    db = make_db(ConnectionFactory(connections))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(db.execute_query, ["SELECT 'a' AS query", "SELECT 'b' AS query"]))

    assert sorted(row[0]["query"] for row in results) == ["a", "b"]
    assert len({id(cursor) for cursor in cursors}) == 2
    assert all(cursor.closed for cursor in cursors)
    assert all(connection.closed for connection in connections)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rows": 0},
        {"query_timeout": -1},
        {"connection_timeout": "5"},
        {"max_fetch_batch": True},
    ],
)
def test_invalid_policy_fails_closed(kwargs):
    values = dict(max_rows=10, query_timeout=10, connection_timeout=5, max_fetch_batch=2)
    values.update(kwargs)
    with pytest.raises(ValueError):
        ExecutionPolicy(**values)


def test_environment_policy_parses_and_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("DB_MAX_ROWS", "25")
    monkeypatch.setenv("DB_MAX_FETCH_BATCH", "5")
    assert ExecutionPolicy.from_environment().max_rows == 25

    monkeypatch.setenv("DB_QUERY_TIMEOUT", "unlimited")
    with pytest.raises(ValueError):
        ExecutionPolicy.from_environment()
