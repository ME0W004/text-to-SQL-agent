import pytest

from src.core.database.connection import DatabaseConnection
from src.core.security.sql_security import SecurityReasonCode, SQLSecurityError


def test_execute_query_checks_security_before_cursor():
    calls = []
    db = DatabaseConnection(
        "host", 1433, "user", "password", "database",
        connect_fn=lambda **kwargs: calls.append(kwargs),
    )

    with pytest.raises(SQLSecurityError) as exc_info:
        db.execute_query("SELECT 1; DROP TABLE x")
    assert exc_info.value.code == SecurityReasonCode.MULTIPLE_STATEMENTS
    assert calls == []


def test_non_query_path_is_disabled_fail_closed():
    db = DatabaseConnection("host", 1433, "user", "password", "database")
    with pytest.raises(SQLSecurityError) as exc_info:
        db.execute_non_query("DELETE FROM x")
    assert exc_info.value.code == SecurityReasonCode.NON_READ_ONLY_STATEMENT
