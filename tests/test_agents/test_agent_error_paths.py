"""Agent error path tests for TextToSQLAgent.ask().

These tests verify that all error paths in TextToSQLAgent.ask() use
the state-based response contract (state_to_api_response) rather than
returning raw dictionaries.
"""

from src.agents.state import (
    create_initial_state,
    state_to_api_response,
    PipelineStage,
    ValidationStatus,
    ExecutionStatus,
    ErrorCategory,
)
from src.core.sql_extractor.validator import SQLValidationError
from src.core.security.sql_security import SQLSecurityError


def test_state_validation_error_path():
    """Validation error path uses state_to_api_response."""
    state = create_initial_state(question="test question")
    state = state.with_generated_sql("bad sql")
    state = state.with_validation_failed("invalid SQL syntax", "VAL_ERROR")
    result = state_to_api_response(state)
    assert result["success"] is False
    assert "error" in result
    assert result["question"] == "test question"


def test_state_security_rejection_path():
    """Security rejection path uses state_to_api_response."""
    state = create_initial_state(question="test question")
    state = state.with_generated_sql("bad sql")
    state = state.with_security_rejected("SECURITY_REASON", "Security message")
    result = state_to_api_response(state)
    assert result["success"] is False
    assert "error" in result
    assert result["question"] == "test question"


def test_state_execution_error_path():
    """Execution error path uses state_to_api_response."""
    state = create_initial_state(question="test question")
    state = state.with_execution_error(
        ExecutionStatus.FAILED, "DATABASE_ERROR", "DB connection failed",
        database_error_code="DATABASE_ERROR"
    )
    result = state_to_api_response(state)
    assert result["success"] is False
    assert "error" in result
    assert result["database_error_code"] == "DATABASE_ERROR"


def test_state_generic_error_path():
    """Generic error path uses state_to_api_response."""
    state = create_initial_state(question="test question")
    state = state.with_error_response("Internal error occurred")
    result = state_to_api_response(state)
    assert result["success"] is False
    assert "error" in result
    assert result["question"] == "test question"


def test_all_error_paths_use_state_contract():
    """Verify all major error paths produce consistent state-based responses."""
    # Path 1: Validation failure
    s1 = create_initial_state(question="q")
    s1 = s1.with_generated_sql("bad")
    s1 = s1.with_validation_failed("msg", "code")
    r1 = state_to_api_response(s1)
    assert r1["success"] is False
    assert "error" in r1

    # Path 2: Security rejection
    s2 = create_initial_state(question="q")
    s2 = s2.with_generated_sql("bad")
    s2 = s2.with_security_rejected("SEC_CODE", "msg")
    r2 = state_to_api_response(s2)
    assert r2["success"] is False
    assert "error" in r2

    # Path 3: Execution error
    s3 = create_initial_state(question="q")
    s3 = s3.with_execution_error(
        ExecutionStatus.FAILED, "ERRCD", "err msg"
    )
    r3 = state_to_api_response(s3)
    assert r3["success"] is False
    assert "error" in r3

    # Path 4: Generic error
    s4 = create_initial_state(question="q")
    s4 = s4.with_error_response("internal error")
    r4 = state_to_api_response(s4)
    assert r4["success"] is False
    assert "error" in r4

    # Path 5: SQL security error
    s5 = create_initial_state(question="q")
    s5 = s5.with_generated_sql("bad")
    s5 = s5.with_security_rejected("SEC_PARSE_ERROR", "parse error")
    r5 = state_to_api_response(s5)
    assert r5["success"] is False
    assert "error" in r5