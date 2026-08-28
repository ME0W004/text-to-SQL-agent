"""AST-based, fail-closed SQL Server security validation.

This module never rewrites SQL and has no database or LLM dependency. The
legacy validator remains responsible for compatibility-facing syntax errors;
this policy is the deterministic security decision at the execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from sqlglot import exp, parse
from sqlglot.errors import ParseError


class SecurityReasonCode(str, Enum):
    SQL_PARSE_ERROR = "SQL_PARSE_ERROR"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    NON_READ_ONLY_STATEMENT = "NON_READ_ONLY_STATEMENT"
    FORBIDDEN_DML = "FORBIDDEN_DML"
    FORBIDDEN_DDL = "FORBIDDEN_DDL"
    FORBIDDEN_EXECUTION = "FORBIDDEN_EXECUTION"
    FORBIDDEN_TRANSACTION_CONTROL = "FORBIDDEN_TRANSACTION_CONTROL"
    UNSUPPORTED_SQL_CONSTRUCT = "UNSUPPORTED_SQL_CONSTRUCT"


class SQLSecurityError(Exception):
    """Typed security rejection; ``sql`` is retained for diagnostics only."""

    def __init__(self, code: Optional[SecurityReasonCode], message: str, sql: Optional[str] = None):
        self.code = code
        self.message = message
        self.sql = sql
        super().__init__(message)


@dataclass(frozen=True)
class SecurityPolicy:
    dialect: str = "tsql"
    allowed_root_types: tuple[type[exp.Expression], ...] = (exp.Select,)
    dml_types: tuple[type[exp.Expression], ...] = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Merge,
    )
    ddl_types: tuple[type[exp.Expression], ...] = (
        exp.Create,
        exp.Alter,
        exp.Drop,
        exp.TruncateTable,
    )
    execution_types: tuple[type[exp.Expression], ...] = (exp.Execute,)
    execution_function_names: frozenset[str] = frozenset({
        "XP_CMDSHELL",
        "SP_EXECUTESQL",
        "OPENROWSET",
        "OPENQUERY",
        "OPENDATASOURCE",
    })
    transaction_types: tuple[type[exp.Expression], ...] = (exp.Transaction, exp.Commit, exp.Rollback, exp.Set)


DEFAULT_SECURITY_POLICY = SecurityPolicy()


@dataclass
class SecurityDecision:
    valid: bool
    reason_code: Optional[SecurityReasonCode] = None
    message: str = "SQL statement accepted."
    statement_type: Optional[str] = None
    statement_count: int = 0
    ast: Optional[exp.Expression] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "message": self.message,
            "statement_type": self.statement_type,
            "statement_count": self.statement_count,
        }


def _reject(code: SecurityReasonCode, message: str, sql: str, ast: exp.Expression, count: int) -> SecurityDecision:
    return SecurityDecision(
        valid=False,
        reason_code=code,
        message=message,
        statement_type=type(ast).__name__,
        statement_count=count,
        ast=ast,
    )


class SQLSecurityValidator:
    """Parse and validate exactly one read-only SQL Server statement."""

    def __init__(self, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY):
        self.policy = policy

    def parse_sql(self, sql: str) -> list[exp.Expression]:
        if not isinstance(sql, str) or not sql.strip():
            raise SQLSecurityError(SecurityReasonCode.SQL_PARSE_ERROR, "SQL query is empty.", sql)
        try:
            statements = parse(sql, dialect=self.policy.dialect)
        except Exception as exc:
            raise SQLSecurityError(SecurityReasonCode.SQL_PARSE_ERROR, f"SQL syntax error: {exc}", sql) from exc
        # sqlglot may emit a standalone Semicolon for a trailing semicolon
        # followed by a comment. That is terminator syntax, not a statement.
        # ``None`` entries, however, represent empty statements and are kept as
        # a signal so ``SELECT 1;;;`` and leading semicolons fail closed.
        if any(statement is None for statement in statements):
            raise SQLSecurityError(
                SecurityReasonCode.MULTIPLE_STATEMENTS,
                "Empty or multiple SQL statements are not allowed.",
                sql,
            )
        statements = [
            statement for statement in statements
            if not isinstance(statement, exp.Semicolon)
        ]
        if not statements:
            raise SQLSecurityError(SecurityReasonCode.SQL_PARSE_ERROR, "No SQL statement was parsed.", sql)
        return statements

    def validate(self, sql: str) -> SecurityDecision:
        try:
            statements = self.parse_sql(sql)
        except SQLSecurityError as error:
            return SecurityDecision(valid=False, reason_code=error.code, message=error.message, statement_count=0)

        if len(statements) != 1:
            ast = statements[0]
            return _reject(
                SecurityReasonCode.MULTIPLE_STATEMENTS,
                "Multiple SQL statements are not allowed.",
                sql,
                ast,
                len(statements),
            )

        ast = statements[0]
        statement_type = type(ast).__name__
        if isinstance(ast, exp.Select) and not ast.expressions:
            return _reject(SecurityReasonCode.SQL_PARSE_ERROR, "SELECT statement has no projection.", sql, ast, 1)
        for node in ast.walk():
            if isinstance(node, self.policy.dml_types):
                return _reject(SecurityReasonCode.FORBIDDEN_DML, f"DML statement is not allowed: {statement_type}.", sql, ast, 1)
            if isinstance(node, self.policy.ddl_types):
                return _reject(SecurityReasonCode.FORBIDDEN_DDL, f"DDL statement is not allowed: {statement_type}.", sql, ast, 1)
            if isinstance(node, self.policy.execution_types):
                return _reject(SecurityReasonCode.FORBIDDEN_EXECUTION, "Procedure or command execution is not allowed.", sql, ast, 1)
            if isinstance(node, self.policy.transaction_types):
                return _reject(SecurityReasonCode.FORBIDDEN_TRANSACTION_CONTROL, "Transaction/session control is not allowed.", sql, ast, 1)
            if isinstance(node, exp.Into):
                return _reject(SecurityReasonCode.FORBIDDEN_DDL, "SELECT INTO is not allowed.", sql, ast, 1)
            if isinstance(node, exp.Anonymous) and str(node.name).upper() in self.policy.execution_function_names:
                code = (
                    SecurityReasonCode.UNSUPPORTED_SQL_CONSTRUCT
                    if str(node.name).upper() in {"OPENROWSET", "OPENQUERY", "OPENDATASOURCE"}
                    else SecurityReasonCode.FORBIDDEN_EXECUTION
                )
                return _reject(code, f"Execution or external access function is not allowed: {node.name}.", sql, ast, 1)

        if not isinstance(ast, self.policy.allowed_root_types):
            return _reject(SecurityReasonCode.NON_READ_ONLY_STATEMENT, f"Root statement is not read-only: {statement_type}.", sql, ast, 1)

        return SecurityDecision(valid=True, statement_type=statement_type, statement_count=1, ast=ast)


def validate_sql_security(sql: str, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY) -> SecurityDecision:
    return SQLSecurityValidator(policy).validate(sql)
