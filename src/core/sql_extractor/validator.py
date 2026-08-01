# src/core/sql_extractor/validator.py

import re
import sqlglot
from sqlglot import parse_one
from sqlglot.errors import ParseError

class SQLValidationError(Exception):
    def __init__(self, message, sql=None):
        self.message = message
        self.sql = sql
        super().__init__(message)

class SQLValidator:
    @staticmethod
    def validate(sql: str) -> bool:
        if not sql or not sql.strip():
            raise SQLValidationError("SQL query is empty.")

        cleaned = sql.strip()
        sql_upper = cleaned.upper()

        # Accept SELECT or WITH (CTE)
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            raise SQLValidationError("Only SELECT queries (including WITH CTEs) are allowed.")

        # Block dangerous keywords
        dangerous_keywords = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "EXEC", "EXECUTE"]
        for keyword in dangerous_keywords:
            if re.search(rf'\b{keyword}\b', sql_upper):
                raise SQLValidationError(f"Query contains forbidden keyword: {keyword}")

        # Parse with sqlglot
        try:
            parsed = parse_one(cleaned, dialect="tsql")
            if parsed is None:
                raise SQLValidationError("Could not parse SQL statement.")
        except ParseError as e:
            raise SQLValidationError(f"SQL syntax error: {e}")
        except Exception as e:
            raise SQLValidationError(f"SQL validation error: {e}")

        return True