import pytest

from src.core.security.sql_security import (
    SecurityReasonCode,
    SQLSecurityValidator,
    validate_sql_security,
)
from src.core.sql_extractor.validator import SQLValidationError, SQLValidator


VALID_SQL = [
    "SELECT * FROM Sale.Orders",
    "SELECT TOP 10 buyer FROM Sale.Orders ORDER BY buyer",
    "WITH x AS (SELECT Id FROM Sale.Orders) SELECT * FROM x",
    "SELECT o.Id, COUNT(*) AS total FROM Sale.Orders AS o GROUP BY o.Id",
    "SELECT o.Id, oi.quantity FROM Sale.Orders o JOIN Sale.OrderItems oi ON o.Id = oi.orderId",
    "SELECT operationMonth, SUM(quantity * unitPrice) AS total FROM Sale.Orders o JOIN Sale.OrderItems oi ON o.Id = oi.orderId GROUP BY operationMonth",
    "SELECT * FROM Sale.Orders OPTION (MAXDOP 1)",
]


@pytest.mark.parametrize("sql", VALID_SQL)
def test_valid_read_only_sql_is_accepted(sql):
    result = validate_sql_security(sql)
    assert result.valid is True
    assert result.reason_code is None
    assert result.statement_count == 1
    assert result.statement_type == "Select"
    assert result.ast is not None


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("SELECT 1; SELECT 2", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; DELETE FROM Sale.Orders", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; UPDATE Sale.Orders SET status = 'x'", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; INSERT INTO Sale.Orders (Id) VALUES (1)", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; DROP TABLE Sale.Orders", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; EXEC dbo.proc", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; ALTER TABLE Sale.Orders ADD x int", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1;;;", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("; SELECT 1", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1; /* comment */ SELECT 2", SecurityReasonCode.MULTIPLE_STATEMENTS),
        ("SELECT 1\nSELECT 2", SecurityReasonCode.SQL_PARSE_ERROR),
    ],
)
def test_multi_statement_variants_are_rejected(sql, code):
    result = validate_sql_security(sql)
    assert result.valid is False
    assert result.reason_code == code


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("INSERT INTO Sale.Orders (Id) VALUES (1)", SecurityReasonCode.FORBIDDEN_DML),
        ("UPDATE Sale.Orders SET status = 'x'", SecurityReasonCode.FORBIDDEN_DML),
        ("DELETE FROM Sale.Orders", SecurityReasonCode.FORBIDDEN_DML),
        ("MERGE Sale.Orders AS target USING Sale.Orders AS source ON 1=0 WHEN NOT MATCHED THEN INSERT (Id) VALUES (1);", SecurityReasonCode.FORBIDDEN_DML),
        ("DROP TABLE Sale.Orders", SecurityReasonCode.FORBIDDEN_DDL),
        ("ALTER TABLE Sale.Orders ADD x int", SecurityReasonCode.FORBIDDEN_DDL),
        ("CREATE TABLE x (id int)", SecurityReasonCode.FORBIDDEN_DDL),
        ("TRUNCATE TABLE Sale.Orders", SecurityReasonCode.FORBIDDEN_DDL),
        ("SELECT 1 INTO #tmp", SecurityReasonCode.FORBIDDEN_DDL),
        ("EXEC dbo.usp_orders", SecurityReasonCode.FORBIDDEN_EXECUTION),
        ("EXECUTE AS USER = 'dbo'", SecurityReasonCode.SQL_PARSE_ERROR),
        ("BEGIN TRANSACTION", SecurityReasonCode.FORBIDDEN_TRANSACTION_CONTROL),
        ("COMMIT TRANSACTION", SecurityReasonCode.FORBIDDEN_TRANSACTION_CONTROL),
        ("ROLLBACK TRANSACTION", SecurityReasonCode.FORBIDDEN_TRANSACTION_CONTROL),
        ("SET NOCOUNT ON", SecurityReasonCode.FORBIDDEN_TRANSACTION_CONTROL),
        ("USE master", SecurityReasonCode.NON_READ_ONLY_STATEMENT),
        ("KILL 53", SecurityReasonCode.NON_READ_ONLY_STATEMENT),
        ("SELECT * FROM OPENQUERY(server, 'SELECT 1')", SecurityReasonCode.UNSUPPORTED_SQL_CONSTRUCT),
        ("SELECT * FROM OPENDATASOURCE('SQLNCLI', 'Data Source=x').db.dbo.t", SecurityReasonCode.UNSUPPORTED_SQL_CONSTRUCT),
        ("SELECT xp_cmdshell('whoami')", SecurityReasonCode.FORBIDDEN_EXECUTION),
        ("SELECT sp_executesql('SELECT 1')", SecurityReasonCode.FORBIDDEN_EXECUTION),
    ],
)
def test_non_read_only_and_execution_constructs_are_rejected(sql, code):
    result = validate_sql_security(sql)
    assert result.valid is False
    assert result.reason_code == code


@pytest.mark.parametrize("sql", ["", "   ", "SELECT", "WITH x AS (SELECT 1)", None])
def test_malformed_sql_fails_closed(sql):
    result = validate_sql_security(sql)
    assert result.valid is False
    assert result.reason_code == SecurityReasonCode.SQL_PARSE_ERROR


def test_unrecognized_root_fails_closed():
    result = validate_sql_security("not SQL")
    assert result.valid is False
    assert result.reason_code == SecurityReasonCode.NON_READ_ONLY_STATEMENT


def test_validator_compatibility_api_raises_typed_legacy_error():
    with pytest.raises(SQLValidationError) as exc_info:
        SQLValidator.validate("SELECT 1; DELETE FROM Sale.Orders")
    assert "MULTIPLE_STATEMENTS" in str(exc_info.value)
    assert exc_info.value.sql == "SELECT 1; DELETE FROM Sale.Orders"
