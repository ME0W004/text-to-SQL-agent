# src/utils/exceptions.py

class TextToSQLException(Exception):
    """Base exception for the application."""
    pass

class ConfigurationError(TextToSQLException):
    """Raised when configuration is missing or invalid."""
    pass

class DatabaseConnectionError(TextToSQLException):
    """Raised when database connection fails."""
    pass

class QueryExecutionError(TextToSQLException):
    """Raised when query execution fails."""
    pass

class SQLValidationError(TextToSQLException):
    """Raised when SQL validation fails."""
    pass

class LLMError(TextToSQLException):
    """Raised when LLM fails to generate response."""
    pass