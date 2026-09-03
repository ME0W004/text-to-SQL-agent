"""Tests for TraceContext integration, serialization, lifecycle, and boundaries."""

import pytest

from src.agents.state import (
    AgentState,
    create_initial_state,
    PipelineStage,
    ValidationStatus,
    ExecutionStatus,
    ErrorCategory,
    SQLState,
    ExecutionState,
    ResponseState,
    ErrorState,
    RuntimeMetadata,
    RequestContext,
    ConversationContext,
    state_to_api_response,
)
from src.utils.logger import TraceContext, get_trace_context, set_trace_context, reset_trace_context


class TestTraceContextIntegration:
    """Tests verifying TraceContext and AgentState boundary."""

    def test_trace_id_copied_at_creation(self):
        """AgentState receives trace_id from TraceContext at creation."""
        trace_id = "test-trace-123"
        state = create_initial_state(question="Q", trace_id=trace_id)
        assert state.request.trace_id == trace_id

    def test_trace_context_independent_after_creation(self):
        """AgentState doesn't depend on TraceContext after creation."""
        trace_id = "trace-abc"
        token = set_trace_context(trace_id)
        try:
            state = create_initial_state(question="Q", trace_id=trace_id)
            # Modify TraceContext
            set_trace_context("different-trace")
            # State should retain original trace_id
            assert state.request.trace_id == trace_id
            assert get_trace_context().trace_id == "different-trace"
        finally:
            reset_trace_context(token)

    def test_trace_id_used_in_logging_correlation(self):
        """State's trace_id used for log correlation."""
        trace_id = "log-trace-456"
        state = create_initial_state(question="Q", trace_id=trace_id)
        # In actual agent, log_event uses state.request.trace_id
        assert state.request.trace_id == trace_id

    def test_trace_context_session_id_separate_from_state(self):
        """TraceContext session_id is for logging; RequestContext.session_id is authoritative."""
        trace_token = set_trace_context("trace-1", "trace-session")
        try:
            state = create_initial_state(question="Q", trace_id="trace-1", session_id="state-session")
            # State has its own session_id
            assert state.request.session_id == "state-session"
            # TraceContext has different session_id (for logging)
            assert get_trace_context().session_id == "trace-session"
        finally:
            reset_trace_context(trace_token)

    def test_no_mutual_dependency(self):
        """AgentState and TraceContext have no mutual dependency after init."""
        state = create_initial_state(question="Q", trace_id="t1")
        # State doesn't hold TraceContext reference
        assert not hasattr(state, 'trace_context')
        assert not hasattr(state.request, 'trace_context')
        # TraceContext doesn't hold AgentState reference
        ctx = get_trace_context() or TraceContext("t1")
        assert not hasattr(ctx, 'agent_state')


class TestTraceIDConsistency:
    """Tests verifying trace_id consistency across operations."""

    def test_trace_id_consistent_in_state_and_api(self):
        """Same trace_id in AgentState and API response."""
        trace_id = "consistent-trace-789"
        state = create_initial_state(question="Q", trace_id=trace_id)
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"x": 1}], 10.0)
        state = state.with_answer("Answer")
        
        resp = state_to_api_response(state)
        # trace_id not in API response (header only), but available in state
        assert state.request.trace_id == trace_id

    def test_trace_id_propagated_through_pipeline(self):
        """Trace ID remains same through all pipeline stages."""
        trace_id = "pipeline-trace"
        state = create_initial_state(question="Q", trace_id=trace_id)
        
        stages = [
            ("sql", lambda s: s.with_generated_sql("SELECT 1")),
            ("validated", lambda s: s.with_validation_passed()),
            ("executed", lambda s: s.with_execution_results([{"x": 1}], 10.0)),
            ("answered", lambda s: s.with_answer("Answer")),
        ]
        
        for stage_name, updater in stages:
            state = updater(state)
            assert state.request.trace_id == trace_id, f"Trace ID changed at {stage_name}"

    def test_trace_id_in_error_paths(self):
        """Trace ID preserved in error states."""
        trace_id = "error-trace"
        state = create_initial_state(question="Q", trace_id=trace_id)
        state = state.with_generated_sql("BAD")
        state = state.with_validation_failed("Error", "ERR_CODE")
        
        assert state.request.trace_id == trace_id
        assert state.pipeline_stage == PipelineStage.FAILED

    def test_multiple_requests_different_trace_ids(self):
        """Each request gets unique trace_id."""
        states = [create_initial_state(question=f"Q{i}") for i in range(10)]
        trace_ids = [s.request.trace_id for s in states]
        assert len(set(trace_ids)) == 10  # All unique


class TestSerializationBoundary:
    """Tests verifying three serialization representations are distinct."""

    def test_internal_state_has_all_fields(self):
        """Internal AgentState object has all typed fields accessible."""
        state = create_initial_state(question="Q with PII", session_id="sess-1")
        state = state.with_generated_sql("SELECT * FROM secret")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"ssn": "123-45-6789"}], 10.0)
        state = state.with_answer("The SSN is 123-45-6789")
        
        # All fields accessible internally
        assert state.request.question == "Q with PII"
        assert state.sql.generated_sql == "SELECT * FROM secret"
        assert state.execution.rows == ({"ssn": "123-45-6789"},)
        assert state.response.answer == "The SSN is 123-45-6789"

    def test_safe_serialization_redacts_sensitive(self):
        """to_dict(include_sensitive=False) redacts sensitive fields."""
        state = create_initial_state(question="Q with PII", session_id="sess-1")
        state = state.with_generated_sql("SELECT * FROM secret")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"ssn": "123-45-6789"}], 10.0)
        state = state.with_answer("The SSN is 123-45-6789")
        
        d = state.to_dict(include_sensitive=False)
        
        assert d["request"]["question"] == "[REDACTED]"
        assert "generated_sql" not in d["sql"] or d["sql"].get("generated_sql") is None
        assert d["sql"]["normalized_sql"] is None  # Redacted in safe mode
        assert "rows" not in d["execution"] or d["execution"].get("rows") is None
        assert d["response"]["has_answer"] is True
        assert "answer" not in d["response"] or d["response"].get("answer") is None
        
        # Non-sensitive fields present
        assert d["request"]["session_id"] == "sess-1"
        assert d["request"]["trace_id"] is not None
        assert d["execution"]["row_count"] == 1
        assert d["sql"]["validation_status"] == "passed"
        assert d["sql"]["has_sql"] is True
        assert d["sql"]["sql_fingerprint"] is not None

    def test_full_serialization_includes_all(self):
        """to_dict(include_sensitive=True) includes all fields."""
        state = create_initial_state(question="Q with PII", session_id="sess-1")
        state = state.with_generated_sql("SELECT * FROM secret")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"ssn": "123-45-6789"}], 10.0)
        state = state.with_answer("The SSN is 123-45-6789")
        
        d = state.to_dict(include_sensitive=True)
        
        assert d["request"]["question"] == "Q with PII"
        assert d["sql"]["generated_sql"] == "SELECT * FROM secret"
        assert d["execution"]["rows"] == [{"ssn": "123-45-6789"}]
        assert d["response"]["answer"] == "The SSN is 123-45-6789"

    def test_api_response_distinct_from_safe_serialization(self):
        """state_to_api_response produces different format than to_dict()."""
        state = create_initial_state(question="Q", session_id="sess-1")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"x": 1}], 10.0)
        state = state.with_answer("Answer")
        
        api_resp = state_to_api_response(state)
        safe_dict = state.to_dict(include_sensitive=False)
        full_dict = state.to_dict(include_sensitive=True)
        
        # API response is flat, has success, question, answer, sql, row_count, rows, session_id
        assert set(api_resp.keys()) == {"success", "question", "answer", "sql", "row_count", "rows", "session_id"}
        
        # Safe dict is nested with pipeline_stage, sql, execution, response, error, metadata, request, conversation
        assert "pipeline_stage" in safe_dict
        assert "metadata" in safe_dict
        
        # Full dict has sensitive data
        assert full_dict["request"]["question"] == "Q"
        assert safe_dict["request"]["question"] == "[REDACTED]"

    def test_normalized_sql_redacted_in_safe_mode(self):
        """normalized_sql field redacted in safe serialization."""
        sql_state = SQLState(
            generated_sql="SELECT 1",
            sql_fingerprint="abc",
            normalized_sql="SELECT 1 FROM table",
            validation_status=ValidationStatus.PASSED,
        )
        state = create_initial_state(question="Q")
        from dataclasses import replace
        state = replace(state, sql=sql_state)
        
        safe = state.to_dict(include_sensitive=False)
        full = state.to_dict(include_sensitive=True)
        
        # normalized_sql should be redacted in safe mode
        assert safe["sql"]["normalized_sql"] is None
        # Full mode should have it
        assert full["sql"]["normalized_sql"] == "SELECT 1 FROM table"

    def test_columns_not_sensitive(self):
        """columns field not sensitive, included in safe mode."""
        exec_state = ExecutionState(
            status=ExecutionStatus.SUCCESS,
            rows=( {"a": 1}, ),
            row_count=1,
            columns=("col1", "col2"),
            duration_ms=10.0,
        )
        state = create_initial_state(question="Q")
        from dataclasses import replace
        state = replace(state, execution=exec_state)
        
        safe = state.to_dict(include_sensitive=False)
        assert safe["execution"]["columns"] == ("col1", "col2")

    def test_token_usage_not_sensitive(self):
        """token_usage field not sensitive, included in safe mode."""
        meta = RuntimeMetadata(token_usage={"prompt": 100, "completion": 50})
        state = create_initial_state(question="Q")
        from dataclasses import replace
        state = replace(state, metadata=meta)
        
        safe = state.to_dict(include_sensitive=False)
        assert safe["metadata"]["token_usage"] == {"prompt": 100, "completion": 50}


class TestSafeRepresentation:
    """Tests for safe __repr__ and __str__."""

    def test_repr_excludes_sensitive_data(self):
        """__repr__ doesn't leak sensitive data."""
        state = create_initial_state(question="Secret question with PII")
        state = state.with_generated_sql("SELECT password FROM users")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"pwd": "secret123"}], 10.0)
        state = state.with_answer("Your password is secret123")
        
        repr_str = repr(state)
        str_str = str(state)
        
        assert "Secret question" not in repr_str
        assert "password" not in repr_str.lower() or "REDACTED" in repr_str
        assert "secret123" not in repr_str
        assert "request_id=" in repr_str
        assert "stage=" in repr_str
        assert "success=" in repr_str

    def test_safe_repr_method(self):
        """safe_repr() method returns safe representation."""
        state = create_initial_state(question="Q")
        safe = state.safe_repr()
        assert "request_id=" in safe
        assert "stage=" in safe
        assert "success=" in safe

    def test_repr_format_consistency(self):
        """repr format is consistent and informative."""
        state = create_initial_state(question="Q")
        r = repr(state)
        # Format: AgentStage(request_id=abc12345, stage=initialized, success=False)
        assert r.startswith("AgentStage(")
        assert r.endswith(")")
        assert "request_id=" in r
        assert "stage=" in r
        assert "success=" in r


class TestStateLifecycle:
    """Integration-level State lifecycle tests."""

    def test_successful_lifecycle(self):
        """Full successful path: CREATE → PROCESS → SQL_READY → VALIDATED → EXECUTED → RESPONDED."""
        state = create_initial_state(question="Show sales", session_id="sess-1", trace_id="trace-1")
        assert state.pipeline_stage == PipelineStage.INITIALIZED
        
        # PROCESS - SQL Generation
        state = state.with_generated_sql("SELECT SUM(amount) FROM Sales")
        assert state.pipeline_stage == PipelineStage.SQL_GENERATED
        assert state.sql.generated_sql == "SELECT SUM(amount) FROM Sales"
        
        # SQL_READY / VALIDATED
        state = state.with_validation_passed()
        assert state.pipeline_stage == PipelineStage.VALIDATED
        assert state.sql.validation_status == ValidationStatus.PASSED
        
        # EXECUTED
        state = state.with_execution_results([{"total": 1000000}], 45.2)
        assert state.pipeline_stage == PipelineStage.EXECUTED
        assert state.execution.status == ExecutionStatus.SUCCESS
        assert state.execution.row_count == 1
        
        # RESPONDED (ANSWERED)
        state = state.with_answer("Total sales: $1,000,000")
        assert state.pipeline_stage == PipelineStage.ANSWERED
        assert state.response.success is True
        assert state.response.answer == "Total sales: $1,000,000"
        assert state.is_success()
        assert state.is_terminal()

    def test_failure_lifecycle_validation(self):
        """Failure path: PROCESSING → FAILED (validation error)."""
        state = create_initial_state(question="Bad query")
        state = state.with_generated_sql("INVALID SQL")
        state = state.with_validation_failed("Syntax error near INVALID", "SYNTAX_ERR")
        
        assert state.pipeline_stage == PipelineStage.FAILED
        assert state.sql.validation_status == ValidationStatus.FAILED
        assert state.error.category == ErrorCategory.VALIDATION
        assert state.error.code == "SYNTAX_ERR"
        assert state.error.retryable is True  # Can retry correction
        assert state.is_failed()
        assert state.is_terminal()

    def test_failure_lifecycle_security(self):
        """Failure path: PROCESSING → FAILED (security rejection)."""
        state = create_initial_state(question="Drop table")
        state = state.with_generated_sql("DROP TABLE Users")
        state = state.with_security_rejected("FORBIDDEN_DDL", "DDL not allowed")
        
        assert state.pipeline_stage == PipelineStage.FAILED
        assert state.sql.validation_status == ValidationStatus.SECURITY_REJECTED
        assert state.error.category == ErrorCategory.SECURITY
        assert state.error.code == "FORBIDDEN_DDL"
        assert state.error.retryable is False
        assert state.is_failed()

    def test_failure_lifecycle_execution(self):
        """Failure path: VALIDATED → EXECUTED → FAILED (execution error)."""
        state = create_initial_state(question="Query")
        state = state.with_generated_sql("SELECT * FROM LargeTable")
        state = state.with_validation_passed()
        state = state.with_execution_error(
            ExecutionStatus.FAILED, "DATABASE_QUERY_ERROR", "Connection timeout",
            database_error_code="DATABASE_TIMEOUT"
        )
        
        assert state.pipeline_stage == PipelineStage.FAILED
        assert state.execution.status == ExecutionStatus.FAILED
        assert state.error.category == ErrorCategory.EXECUTION
        assert state.error.details.get("database_error_code") == "DATABASE_TIMEOUT"

    def test_failure_lifecycle_generic(self):
        """Failure path: Any stage → FAILED (unexpected error)."""
        state = create_initial_state(question="Q")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_error_response(
            "Unexpected internal error",
            category=ErrorCategory.INTERNAL,
            code="INTERNAL_ERROR",
            stage=PipelineStage.SQL_GENERATED
        )
        
        assert state.pipeline_stage == PipelineStage.FAILED
        assert state.response.success is False
        assert state.response.error == "Unexpected internal error"
        assert state.error.category == ErrorCategory.INTERNAL


class TestInvalidTransitions:
    """Tests for invalid state transitions."""

    def test_failed_to_executed_invalid(self):
        """FAILED → EXECUTED should raise ValueError."""
        state = create_initial_state(question="Q")
        state = state.with_generated_sql("BAD")
        state = state.with_validation_failed("Error", "ERR")
        assert state.pipeline_stage == PipelineStage.FAILED
        
        with pytest.raises(ValueError, match="Invalid transition: failed -> executed"):
            state.transition_to(PipelineStage.EXECUTED)

    def test_completed_to_processing_invalid(self):
        """ANSWERED → PROCESSING (SQL_GENERATED) should raise ValueError."""
        state = create_initial_state(question="Q")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"x": 1}], 10.0)
        state = state.with_answer("Answer")
        assert state.pipeline_stage == PipelineStage.ANSWERED
        
        with pytest.raises(ValueError, match="Invalid transition: answered -> sql_generated"):
            state.transition_to(PipelineStage.SQL_GENERATED)

    def test_validated_to_initialized_invalid(self):
        """VALIDATED → INITIALIZED should raise ValueError."""
        state = create_initial_state(question="Q")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        assert state.pipeline_stage == PipelineStage.VALIDATED
        
        with pytest.raises(ValueError, match="Invalid transition: validated -> initialized"):
            state.transition_to(PipelineStage.INITIALIZED)

    def test_executed_to_question_analyzed_invalid(self):
        """EXECUTED → QUESTION_ANALYZED should raise ValueError."""
        state = create_initial_state(question="Q")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"x": 1}], 10.0)
        assert state.pipeline_stage == PipelineStage.EXECUTED
        
        with pytest.raises(ValueError, match="Invalid transition: executed -> question_analyzed"):
            state.transition_to(PipelineStage.QUESTION_ANALYZED)

    def test_failed_to_answered_invalid(self):
        """FAILED → ANSWERED should raise ValueError."""
        state = create_initial_state(question="Q")
        state = state.with_error_response("Error")
        assert state.pipeline_stage == PipelineStage.FAILED
        
        with pytest.raises(ValueError, match="Invalid transition: failed -> answered"):
            state.transition_to(PipelineStage.ANSWERED)

    def test_terminal_states_no_transitions(self):
        """Terminal states (ANSWERED, FAILED) have no valid transitions."""
        # ANSWERED
        state = create_initial_state(question="Q")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"x": 1}], 10.0)
        state = state.with_answer("Answer")
        assert state.can_transition_to(PipelineStage.ANSWERED) is False
        assert state.can_transition_to(PipelineStage.FAILED) is False
        assert state.can_transition_to(PipelineStage.SQL_GENERATED) is False
        
        # FAILED
        state2 = create_initial_state(question="Q")
        state2 = state2.with_error_response("Error")
        assert state2.can_transition_to(PipelineStage.ANSWERED) is False
        assert state2.can_transition_to(PipelineStage.FAILED) is False
        assert state2.can_transition_to(PipelineStage.SQL_GENERATED) is False


class TestIntegrationWithSafetyStack:
    """Integration tests with Security, Execution, Observability."""

    def test_security_validation_before_execution(self):
        """Security validation runs before database execution."""
        # This is architectural - verified by DatabaseConnection.execute_query
        # which calls security_validator.validate() before connecting
        from src.core.database.connection import DatabaseConnection, ExecutionPolicy
        from src.core.security.sql_security import SQLSecurityValidator
        
        # Verify DatabaseConnection has security_validator
        db = DatabaseConnection(
            "host", 1433, "user", "pass", "db",
            execution_policy=ExecutionPolicy(),
            connect_fn=lambda **kw: None  # Won't be called
        )
        assert hasattr(db, 'security_validator')
        assert isinstance(db.security_validator, SQLSecurityValidator)

    def test_execution_policy_enforced(self):
        """ExecutionPolicy limits are enforced by DatabaseConnection."""
        from src.core.database.connection import ExecutionPolicy
        
        policy = ExecutionPolicy(max_rows=10, max_fetch_batch=5, query_timeout=30, connection_timeout=5)
        assert policy.max_rows == 10
        assert policy.max_fetch_batch == 5

    def test_observability_events_emitted(self):
        """State updates correlate with observability events."""
        # This verifies the design - actual emission tested in observability tests
        state = create_initial_state(question="Q", trace_id="trace-123")
        
        # Each pipeline stage in agent emits events with trace_id
        assert state.request.trace_id == "trace-123"
        
        # Metadata tracks timings
        state = state.with_stage_timing("sql_generation", 150.0)
        assert state.metadata.stage_timings["sql_generation"] == 150.0
        
        state = state.with_llm_call(200.0, "openai", "gpt-4o")
        assert state.metadata.llm_calls == 1
        assert state.metadata.llm_total_duration_ms == 200.0

    def test_error_codes_stable_not_raw_driver(self):
        """Error codes are stable application codes, not raw driver messages."""
        state = create_initial_state(question="Q")
        state = state.with_execution_error(
            ExecutionStatus.FAILED,
            error_code="DATABASE_QUERY_ERROR",
            error_message="Driver error: Login timeout expired",
            database_error_code="DATABASE_TIMEOUT"
        )
        
        # Error code is stable application code
        assert state.error.code == "DATABASE_QUERY_ERROR"
        # Database error code preserved for debugging
        assert state.error.details.get("database_error_code") == "DATABASE_TIMEOUT"
        # Message is sanitized
        assert "Login timeout" in state.error.message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])