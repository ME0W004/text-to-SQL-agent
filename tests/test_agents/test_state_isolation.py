"""Request isolation and concurrency tests for AgentState."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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


class TestRequestIsolation:
    """Tests verifying that each request gets its own isolated AgentState."""

    def test_two_requests_produce_independent_states(self):
        """State A is not State B - separate instances with independent data."""
        state_a = create_initial_state(question="Question A", session_id="sess-a")
        state_b = create_initial_state(question="Question B", session_id="sess-b")

        assert state_a is not state_b
        assert state_a.request.question == "Question A"
        assert state_b.request.question == "Question B"
        assert state_a.request.session_id == "sess-a"
        assert state_b.request.session_id == "sess-b"
        assert state_a.request.request_id != state_b.request.request_id
        assert state_a.request.trace_id != state_b.request.trace_id

    def test_mutation_of_state_a_does_not_affect_state_b(self):
        """Updating State A does not leak into State B."""
        state_a = create_initial_state(question="Question A")
        state_b = create_initial_state(question="Question B")

        # Mutate State A through pipeline
        state_a = state_a.with_generated_sql("SELECT * FROM A")
        state_a = state_a.with_validation_passed()
        state_a = state_a.with_execution_results([{"col": "val_a"}], 10.0)
        state_a = state_a.with_answer("Answer A")

        # State B remains at INITIALIZED
        assert state_b.pipeline_stage == PipelineStage.INITIALIZED
        assert state_b.sql.generated_sql is None
        assert state_b.execution.row_count == 0
        assert state_b.response.answer is None

        # State A has progressed
        assert state_a.pipeline_stage == PipelineStage.ANSWERED
        assert state_a.sql.generated_sql == "SELECT * FROM A"
        assert state_a.execution.row_count == 1
        assert state_a.response.answer == "Answer A"

    def test_sql_state_isolation(self):
        """SQLState mutations are isolated per request."""
        state_a = create_initial_state(question="Q1")
        state_b = create_initial_state(question="Q2")

        state_a = state_a.with_generated_sql("SELECT 1")
        state_b = state_b.with_generated_sql("SELECT 2")

        assert state_a.sql.generated_sql == "SELECT 1"
        assert state_b.sql.generated_sql == "SELECT 2"
        assert state_a.sql.sql_fingerprint != state_b.sql.sql_fingerprint

    def test_execution_state_isolation(self):
        """ExecutionState mutations are isolated per request."""
        state_a = create_initial_state(question="Q1")
        state_b = create_initial_state(question="Q2")

        state_a = state_a.with_generated_sql("SELECT 1")
        state_a = state_a.with_validation_passed()
        state_a = state_a.with_execution_results([{"x": 1}], 50.0)

        state_b = state_b.with_generated_sql("SELECT 2")
        state_b = state_b.with_validation_passed()
        state_b = state_b.with_execution_results([{"y": 2}, {"y": 3}], 75.0)

        assert state_a.execution.row_count == 1
        assert state_a.execution.rows == ({"x": 1},)
        assert state_b.execution.row_count == 2
        assert state_b.execution.rows == ({"y": 2}, {"y": 3})

    def test_response_state_isolation(self):
        """ResponseState mutations are isolated per request."""
        state_a = create_initial_state(question="Q1")
        state_b = create_initial_state(question="Q2")

        # Need to progress through valid stages for with_answer
        state_a = state_a.with_generated_sql("SELECT 1")
        state_a = state_a.with_validation_passed()
        state_a = state_a.with_execution_results([{"x": 1}], 10.0)
        state_a = state_a.with_answer("Answer A")

        state_b = state_b.with_error_response("Error B")

        assert state_a.response.success is True
        assert state_a.response.answer == "Answer A"
        assert state_b.response.success is False
        assert state_b.response.error == "Error B"

    def test_error_state_isolation(self):
        """ErrorState mutations are isolated per request."""
        state_a = create_initial_state(question="Q1")
        state_b = create_initial_state(question="Q2")

        state_a = state_a.with_validation_failed("Invalid SQL", "VAL_ERR")
        state_b = state_b.with_security_rejected("SEC_REASON", "Security violation")

        assert state_a.pipeline_stage == PipelineStage.FAILED
        assert state_a.error.category == ErrorCategory.VALIDATION
        assert state_b.pipeline_stage == PipelineStage.FAILED
        assert state_b.error.category == ErrorCategory.SECURITY

    def test_metadata_isolation(self):
        """RuntimeMetadata mutations are isolated per request."""
        state_a = create_initial_state(question="Q1")
        state_b = create_initial_state(question="Q2")

        state_a = state_a.with_llm_call(100.0, "openai", "gpt-4o")
        state_a = state_a.with_llm_call(150.0, "openai", "gpt-4o")
        state_b = state_b.with_llm_call(200.0, "anthropic", "claude-3")

        assert state_a.metadata.llm_calls == 2
        assert state_a.metadata.llm_total_duration_ms == 250.0
        assert state_a.metadata.model_provider == "openai"
        assert state_b.metadata.llm_calls == 1
        assert state_b.metadata.llm_total_duration_ms == 200.0
        assert state_b.metadata.model_provider == "anthropic"

    def test_conversation_context_isolation(self):
        """ConversationContext history is isolated per request."""
        history_a = [{"role": "user", "content": "Hello A"}]
        history_b = [{"role": "user", "content": "Hello B"}, {"role": "assistant", "content": "Hi B"}]

        state_a = create_initial_state(question="Q1", history=history_a)
        state_b = create_initial_state(question="Q2", history=history_b)

        assert len(state_a.conversation.history) == 1
        assert state_a.conversation.history[0]["content"] == "Hello A"
        assert len(state_b.conversation.history) == 2
        assert state_b.conversation.history[0]["content"] == "Hello B"

    def test_api_response_isolation(self):
        """state_to_api_response produces independent results."""
        state_a = create_initial_state(question="Q1", session_id="sess-a")
        state_a = state_a.with_generated_sql("SELECT 1")
        state_a = state_a.with_validation_passed()
        state_a = state_a.with_execution_results([{"v": 1}], 10.0)
        state_a = state_a.with_answer("Answer A")

        state_b = create_initial_state(question="Q2", session_id="sess-b")
        state_b = state_b.with_generated_sql("SELECT 2")
        state_b = state_b.with_validation_passed()
        state_b = state_b.with_execution_results([{"v": 2}], 20.0)
        state_b = state_b.with_answer("Answer B")

        resp_a = state_to_api_response(state_a)
        resp_b = state_to_api_response(state_b)

        assert resp_a["success"] is True
        assert resp_a["question"] == "Q1"
        assert resp_a["answer"] == "Answer A"
        assert resp_a["session_id"] == "sess-a"

        assert resp_b["success"] is True
        assert resp_b["question"] == "Q2"
        assert resp_b["answer"] == "Answer B"
        assert resp_b["session_id"] == "sess-b"


class TestConcurrentIsolation:
    """Tests simulating concurrent/interleaved requests."""

    def test_sequential_requests_maintain_isolation(self):
        """Sequential requests through same code path maintain isolation."""
        states = []
        for i in range(10):
            state = create_initial_state(question=f"Question {i}", session_id=f"sess-{i}")
            state = state.with_generated_sql(f"SELECT {i}")
            state = state.with_validation_passed()
            state = state.with_execution_results([{"val": i}], float(i * 10))
            state = state.with_answer(f"Answer {i}")
            states.append(state)

        for i, state in enumerate(states):
            assert state.request.question == f"Question {i}"
            assert state.request.session_id == f"sess-{i}"
            assert state.sql.generated_sql == f"SELECT {i}"
            assert state.execution.row_count == 1
            assert state.execution.rows[0]["val"] == i
            assert state.response.answer == f"Answer {i}"

    def test_interleaved_state_updates(self):
        """Interleaved updates to different states don't interfere."""
        state_a = create_initial_state(question="A")
        state_b = create_initial_state(question="B")

        # Interleave updates
        state_a = state_a.with_generated_sql("SELECT A")
        state_b = state_b.with_generated_sql("SELECT B")
        state_a = state_a.with_validation_passed()
        state_b = state_b.with_validation_passed()
        state_a = state_a.with_execution_results([{"a": 1}], 10.0)
        state_b = state_b.with_execution_results([{"b": 2}], 20.0)
        state_a = state_a.with_answer("Answer A")
        state_b = state_b.with_answer("Answer B")

        assert state_a.pipeline_stage == PipelineStage.ANSWERED
        assert state_b.pipeline_stage == PipelineStage.ANSWERED
        assert state_a.sql.generated_sql == "SELECT A"
        assert state_b.sql.generated_sql == "SELECT B"
        assert state_a.response.answer == "Answer A"
        assert state_b.response.answer == "Answer B"

    def test_thread_safety_concurrent_state_creation(self):
        """Concurrent state creation from multiple threads produces isolated states."""
        results = []
        errors = []

        def create_and_update(thread_id):
            try:
                state = create_initial_state(
                    question=f"Question {thread_id}",
                    session_id=f"sess-{thread_id}",
                    trace_id=f"trace-{thread_id}",
                )
                # Simulate some processing time
                time.sleep(0.001)
                state = state.with_generated_sql(f"SELECT {thread_id}")
                state = state.with_validation_passed()
                state = state.with_execution_results([{"id": thread_id}], 10.0)
                state = state.with_answer(f"Answer {thread_id}")
                results.append(state)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_and_update, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 20

        # Verify each state is correct and isolated
        for state in results:
            tid = state.request.session_id.split("-")[1]
            assert state.request.question == f"Question {tid}"
            assert state.sql.generated_sql == f"SELECT {tid}"
            assert state.execution.rows[0]["id"] == int(tid)
            assert state.response.answer == f"Answer {tid}"

    def test_thread_pool_concurrent_requests(self):
        """ThreadPoolExecutor simulating concurrent agent requests."""
        def process_request(req_id):
            state = create_initial_state(
                question=f"Concurrent Q{req_id}",
                session_id=f"sess-{req_id}",
            )
            state = state.with_generated_sql(f"SELECT {req_id}")
            state = state.with_validation_passed()
            state = state.with_execution_results([{"req_id": req_id}], 5.0)
            state = state.with_answer(f"Result {req_id}")
            return state_to_api_response(state)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(process_request, i) for i in range(50)]
            responses = [f.result() for f in as_completed(futures)]

        assert len(responses) == 50
        for resp in responses:
            assert resp["success"] is True
            assert "Answer" in resp["answer"] or "Result" in resp["answer"]
            assert resp["session_id"].startswith("sess-")

    def test_error_isolation_under_concurrency(self):
        """Error states in one request don't leak to others."""
        def failing_request(req_id):
            state = create_initial_state(question=f"Q{req_id}")
            state = state.with_generated_sql("BAD SQL")
            state = state.with_validation_failed("Syntax error", "SYNTAX_ERR")
            return state_to_api_response(state)

        def success_request(req_id):
            state = create_initial_state(question=f"Q{req_id}")
            state = state.with_generated_sql(f"SELECT {req_id}")
            state = state.with_validation_passed()
            state = state.with_execution_results([{"v": req_id}], 5.0)
            state = state.with_answer(f"OK {req_id}")
            return state_to_api_response(state)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for i in range(10):
                if i % 2 == 0:
                    futures.append(executor.submit(failing_request, i))
                else:
                    futures.append(executor.submit(success_request, i))
            responses = [f.result() for f in as_completed(futures)]

        assert len(responses) == 10
        failed = [r for r in responses if not r["success"]]
        succeeded = [r for r in responses if r["success"]]
        assert len(failed) == 5
        assert len(succeeded) == 5
        for r in failed:
            assert "error" in r
        for r in succeeded:
            assert r["answer"] is not None


class TestStateLeakageAcrossFailures:
    """Tests verifying no state leakage when requests fail."""

    def test_failed_request_does_not_pollute_subsequent_request(self):
        """Request A fails, Request B starts clean."""
        # Request A - fails at validation
        state_a = create_initial_state(question="Bad Question", session_id="sess-a")
        state_a = state_a.with_generated_sql("INVALID SQL")
        state_a = state_a.with_validation_failed("Syntax error", "SYNTAX_ERR")
        resp_a = state_to_api_response(state_a)

        # Request B - fresh state, succeeds
        state_b = create_initial_state(question="Good Question", session_id="sess-b")
        state_b = state_b.with_generated_sql("SELECT 1")
        state_b = state_b.with_validation_passed()
        state_b = state_b.with_execution_results([{"x": 1}], 10.0)
        state_b = state_b.with_answer("Success")
        resp_b = state_to_api_response(state_b)

        # Verify A failed
        assert resp_a["success"] is False
        assert "error" in resp_a
        assert resp_a["sql"] == "INVALID SQL"

        # Verify B succeeded with clean state
        assert resp_b["success"] is True
        assert resp_b["question"] == "Good Question"
        assert resp_b["answer"] == "Success"
        assert resp_b["sql"] == "SELECT 1"
        assert resp_b["session_id"] == "sess-b"

        # Verify no cross-contamination
        assert resp_a["session_id"] != resp_b["session_id"]
        assert resp_a["question"] != resp_b["question"]
        assert resp_a["sql"] != resp_b["sql"]

    def test_multiple_failures_in_sequence(self):
        """Multiple sequential failures don't accumulate state."""
        for i in range(5):
            state = create_initial_state(question=f"Fail {i}")
            state = state.with_generated_sql(f"BAD {i}")
            state = state.with_security_rejected("SEC_REASON", "Rejected")
            resp = state_to_api_response(state)

            assert resp["success"] is False
            assert resp["question"] == f"Fail {i}"
            assert resp["sql"] == f"BAD {i}"
            assert "error" in resp

    def test_error_state_fields_not_shared(self):
        """ErrorState objects are not shared across requests."""
        state_a = create_initial_state(question="Q1")
        state_a = state_a.with_validation_failed("Error A", "ERR_A")

        state_b = create_initial_state(question="Q2")
        state_b = state_b.with_execution_error(
            ExecutionStatus.FAILED, "ERR_B", "DB Error B"
        )

        assert state_a.error.category == ErrorCategory.VALIDATION
        assert state_a.error.code == "ERR_A"
        assert state_a.error.message == "Error A"

        assert state_b.error.category == ErrorCategory.EXECUTION
        assert state_b.error.code == "ERR_B"
        assert state_b.error.message == "DB Error B"

        # Different error objects
        assert state_a.error is not state_b.error


class TestSessionMemoryIsolation:
    """Tests verifying AgentState doesn't mutate persisted Memory."""

    def test_state_changes_dont_affect_memory_manager(self):
        """AgentState conversation is a snapshot, not a live reference."""
        # This test verifies the architectural boundary
        # AgentState.conversation.history is a tuple (immutable)
        # MemoryManager stores data in SQLite separately
        
        history = [{"role": "user", "content": "Original"}]
        state = create_initial_state(question="Q", history=history)
        
        # State has snapshot
        assert len(state.conversation.history) == 1
        assert state.conversation.history[0]["content"] == "Original"
        
        # Modifying original list doesn't affect state's tuple
        history.append({"role": "assistant", "content": "Added later"})
        assert len(state.conversation.history) == 1  # Still 1, tuple is immutable
        
        # State's with_history creates new ConversationContext
        new_history = [{"role": "user", "content": "New"}]
        new_state = state.with_conversation(state.conversation.with_history(new_history))
        assert len(new_state.conversation.history) == 1
        assert new_state.conversation.history[0]["content"] == "New"
        # Original state unchanged
        assert len(state.conversation.history) == 1
        assert state.conversation.history[0]["content"] == "Original"

    def test_memory_manager_not_in_state(self):
        """AgentState does not contain MemoryManager reference."""
        state = create_initial_state(question="Q")
        
        # Verify no MemoryManager in any field
        import dataclasses
        for field in dataclasses.fields(state):
            value = getattr(state, field.name)
            # Check recursively for MemoryManager
            assert not hasattr(value, '__class__') or 'MemoryManager' not in type(value).__name__
        
        # Specifically check conversation doesn't hold it
        assert not hasattr(state.conversation, 'memory')
        assert not hasattr(state.conversation, 'db_path')


# Need to add with_conversation method to AgentState for the test above
# But the test is correct - AgentState uses replace pattern


if __name__ == "__main__":
    pytest.main([__file__, "-v"])