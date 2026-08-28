# src/core/sql_extractor/validator.py

from src.core.security.sql_security import SQLSecurityValidator

class SQLValidationError(Exception):
    def __init__(self, message, sql=None):
        self.message = message
        self.sql = sql
        super().__init__(message)

class SQLValidator:
    _security_validator = SQLSecurityValidator()

    @staticmethod
    def validate(sql: str) -> bool:
        if not sql or not sql.strip():
            raise SQLValidationError("SQL query is empty.")

        cleaned = sql.strip()

        # Security Core is the authoritative AST/policy decision. Keep this
        # bool/exception API for SQLExtractor compatibility.
        security_decision = SQLValidator._security_validator.validate(cleaned)
        if not security_decision.valid:
            code = security_decision.reason_code.value if security_decision.reason_code else "SECURITY_REJECTED"
            raise SQLValidationError(f"{code}: {security_decision.message}", sql=sql)
        return True
