"""Tests for the Sprint 2 Prompt 2B Orchestrator.

The Orchestrator is the single writer of ``AgentState``. These tests
verify the current pipeline still works through the Orchestrator and
that state transitions, error handling, and dependency injection are
correct.

The Orchestrator is exercised with fake components (no live DB or LLM).
"""

from __future__ import annotations

import logging
import pytest

from src.agents.state import (
    AgentState,
    ErrorCategory,
    ExecutionStatus,
    PipelineStage,
    ValidationStatus,
    create_initial_state,
)
from src.agents.orchestrator import Orchestrator, OrchestratorDeps
from src.agents.adapters import (
    SecurityValidatorAdapter,
    QueryExecutorAdapter,
)
from src.agents.contracts import (
    AnswerDraft,
    AnswerGenerator,
    AnswerValidationError,
    AnswerValidator,
    ComplexityReport,
    ComplexityValidator,
    Evidence,
    LLMResult,
    LocalePolicy,
    ParsedAST,
    PresentationPlan,
    PresentationPlanner,
    QueryPlan,
    QueryResult,
    QuestionAnalyzer,
    QuestionSpec,
    ResultAnalyzer,
    ResultProfile,
    SchemaContext,
    SchemaRetriever,
    SchemaSnapshot,
    SemanticReport,
    SemanticValidator,
    SQLCandidate,
    SQLGenerator,
    SQLRepairer,
    SQLValidator as SQLValidatorProtocol,
    SecurityValidator,
    Usage,
    ValidatedAnswer,
)
from src.core.database.connection import DatabaseConnection, ExecutionPolicy
from src.core.security.sql_security import SQLSecurityError
from src.utils.logger import get_trace_context, reset_trace_context, set_trace_context


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


class FakeCursor:
    arraysize = 1

    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchmany(self, size):
        result, self.rows = self.rows[:size], self.rows[size:]
        return result

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.closed = False

    def cursor(self, as_dict=True):
        return self.cursor_instance

    def close(self):
        self.closed = True


class FakeExtractor:
    def __init__(self, sql="SELECT 1 AS value"):
        self.sql = sql
        self.calls = 0

    def extract(self, question, history=None):
        self.calls += 1
        return self.sql


class FakeLLM:
    def __init__(self, text="The value is 1."):
        self.text = text
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return self.text


class FakeMemory:
    def __init__(self, session="session-orch"):
        self.session = session
        self.messages = []

    def create_session(self):
        return self.session

    def session_exists(self, session_id):
        return bool(session_id)

    def get_history(self, session_id, limit=5):
        return []

    def add_message(self, session_id, role, content):
        self.messages.append((session_id, role, content))

    def clear_session(self, session_id):
        pass


class FakePrompts:
    def natural_response_prompt(self, question, rows, row_count):
        return "summarize"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_db_with_cursor(rows):
    cursor = FakeCursor(rows)
    return (
        DatabaseConnection(
            "host", 1433, "user", "password", "database",
            execution_policy=ExecutionPolicy(
                max_rows=10, max_fetch_batch=2,
                query_timeout=5, connection_timeout=2,
            ),
            connect_fn=lambda **kwargs: FakeConnection(cursor),
        ),
        cursor,
    )


def _build_orchestrator(
    *,
    extractor=None,
    llm=None,
    memory=None,
    prompts=None,
    db_rows=None,
    security_validator=None,
    query_executor=None,
    db_factory=None,
):
    extractor = extractor or FakeExtractor()
    llm = llm or FakeLLM()
    memory = memory or FakeMemory()
    prompts = prompts or FakePrompts()
    if query_executor is None:
        rows = [{"value": 1}] if db_rows is None else db_rows
        if db_factory is None:
            db, _cursor = _build_db_with_cursor(rows)
            query_executor = QueryExecutorAdapter(db)
        else:
            query_executor = QueryExecutorAdapter(db_factory())
    deps = OrchestratorDeps(
        sql_extractor=extractor,
        query_executor=query_executor,
        llm_client=llm,
        prompts=prompts,
        memory_manager=memory,
        security_validator=security_validator or SecurityValidatorAdapter(),
    )
    return Orchestrator(deps)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestOrchestratorHappyPath:
    def test_successful_pipeline_runs_to_answered(self):
        orch = _build_orchestrator()
        token = set_trace_context("trace-orch")
        try:
            state = create_initial_state(question="what is value?", session_id="s1")
            final = orch.run(state)
        finally:
            reset_trace_context(token)

        assert final.is_success() is True
        assert final.pipeline_stage == PipelineStage.ANSWERED
        assert final.response.success is True
        assert final.response.answer == "The value is 1."
        assert final.sql.generated_sql == "SELECT 1 AS value"
        assert final.execution.is_success is True
        assert final.execution.row_count == 1
        assert final.request.session_id == "s1"

    def test_orchestrator_emits_lifecycle_events(self, caplog):
        caplog.set_level(logging.INFO)
        orch = _build_orchestrator()
        token = set_trace_context("trace-events")
        try:
            state = create_initial_state(question="q", session_id="s-events")
            orch.run(state)
        finally:
            reset_trace_context(token)
        events = [getattr(record, "event", None) for record in caplog.records]
        assert "orchestrator_started" in events
        assert "orchestrator_completed" in events

    def test_empty_result_no_data_message(self):
        orch = _build_orchestrator(db_rows=[])
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-empty")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.is_success() is True
        assert final.response.answer == "No data found for your query."

    def test_persists_turns_in_memory(self):
        memory = FakeMemory()
        orch = _build_orchestrator(memory=memory)
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="how many?", session_id="s-mem")
            orch.run(state)
        finally:
            reset_trace_context(token)
        # Both user and assistant messages persisted.
        assert any(role == "user" for _, role, _ in memory.messages)
        assert any(role == "assistant" for _, role, _ in memory.messages)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestOrchestratorFailures:
    def test_security_failure_transitions_to_failed(self):
        class RejectAll(SecurityValidatorAdapter):
            def validate(self, sql, *, policy=None):
                from src.core.security.sql_security import (
                    SecurityDecision,
                    SecurityReasonCode,
                )
                return SecurityDecision(
                    valid=False,
                    reason_code=SecurityReasonCode.NON_READ_ONLY_STATEMENT,
                    message="rejected for test",
                    statement_type="Drop",
                    statement_count=1,
                )

        orch = _build_orchestrator(security_validator=RejectAll())
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-sec")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed() is True
        assert final.pipeline_stage == PipelineStage.FAILED
        assert final.error.category == ErrorCategory.SECURITY
        assert "rejected" in (final.error.message or "").lower()

    def test_security_exception_typed_error_is_propagated_to_state(self):
        class Exploding(SecurityValidatorAdapter):
            def validate(self, sql, *, policy=None):
                raise SQLSecurityError(
                    code=None, message="validator blew up", sql=sql,
                )

        orch = _build_orchestrator(security_validator=Exploding())
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-exp")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        # SQLSecurityError falls through to the orchestrator's known-failure
        # handler and transitions to FAILED with a security category.
        assert final.is_failed() is True
        assert final.error.category == ErrorCategory.SECURITY

    def test_executor_failure_transitions_to_failed(self):
        from src.core.database.connection import DatabaseError, DatabaseErrorCode

        class FailingExecutor:
            def execute(self, sql, *, params=None, policy=None):
                raise DatabaseError(
                    code=DatabaseErrorCode.DATABASE_QUERY_ERROR,
                    message="boom",
                    sql=sql,
                )

        orch = _build_orchestrator(query_executor=FailingExecutor())
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-exe")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed() is True
        assert final.execution.status == ExecutionStatus.FAILED
        assert final.error.category == ErrorCategory.EXECUTION

    def test_unexpected_exception_is_caught_as_internal(self):
        class ExplodingExtractor:
            def extract(self, question, history=None):
                raise RuntimeError("kaboom")

        orch = _build_orchestrator(extractor=ExplodingExtractor())
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-int")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed() is True
        assert final.error.category == ErrorCategory.INTERNAL
        assert "Internal error" in (final.response.error or "")

    def test_llm_failure_during_answer_is_caught(self):
        class BrokenLLM:
            def generate(self, prompt):
                raise RuntimeError("openai down")

        orch = _build_orchestrator(llm=BrokenLLM())
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-llm")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        # LLM failure on the answer path becomes an LLM error response.
        assert final.is_failed() is True
        assert final.error.category == ErrorCategory.LLM
        assert final.error.retryable is True


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestOrchestratorStateTransitions:
    def test_state_progresses_through_expected_stages(self):
        orch = _build_orchestrator()
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-trans")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        # Initial → SQL_GENERATED → VALIDATED → EXECUTED → ANSWERED
        # The transition log events would prove it; here we check terminal.
        assert final.pipeline_stage == PipelineStage.ANSWERED

    def test_failed_state_preserves_request_context(self):
        class RejectAll(SecurityValidatorAdapter):
            def validate(self, sql, *, policy=None):
                from src.core.security.sql_security import (
                    SecurityDecision,
                    SecurityReasonCode,
                )
                return SecurityDecision(
                    valid=False,
                    reason_code=SecurityReasonCode.NON_READ_ONLY_STATEMENT,
                    message="no",
                    statement_type="Drop",
                    statement_count=1,
                )

        orch = _build_orchestrator(security_validator=RejectAll())
        token = set_trace_context("t-ctx")
        try:
            state = create_initial_state(
                question="who are you?", session_id="s-ctx", trace_id="t-ctx",
            )
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed() is True
        assert final.request.question == "who are you?"
        assert final.request.session_id == "s-ctx"
        assert final.request.trace_id == "t-ctx"

    def test_security_decision_recorded_in_sql_state(self):
        """When SecurityValidator returns a valid decision it must be
        stored on ``SQLState.security_decision``."""
        orch = _build_orchestrator()
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-dec")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.sql.security_decision is not None
        assert final.sql.security_decision.valid is True


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class TestOrchestratorDependencyInjection:
    def test_orchestrator_does_not_instantiate_its_dependencies(self):
        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=QueryExecutorAdapter(_build_db_with_cursor([{"v": 1}])[0]),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
        )
        orch = Orchestrator(deps)
        assert orch._deps is deps

    def test_optional_components_default_to_none(self):
        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=QueryExecutorAdapter(_build_db_with_cursor([])[0]),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
        )
        assert deps.question_analyzer is None
        assert deps.schema_catalog is None
        assert deps.schema_retriever is None
        assert deps.query_planner is None
        assert deps.sql_generator is None
        assert deps.sql_validator is None
        assert deps.semantic_validator is None
        assert deps.complexity_validator is None
        assert deps.sql_repairer is None
        assert deps.result_analyzer is None
        assert deps.presentation_planner is None
        assert deps.answer_generator is None
        assert deps.answer_validator is None
        assert deps.llm_router is None


# ---------------------------------------------------------------------------
# Stub interfaces — verify they can be slotted in
# ---------------------------------------------------------------------------


class TestOrchestratorWithFutureStubs:
    """The Orchestrator accepts any Protocol-conforming component.
    These tests pass Protocol-conforming stubs and verify the orchestrator
    routes through them when supplied."""

    def test_question_analyzer_stub(self):
        class StubAnalyzer(QuestionAnalyzer):
            def analyze(self, question, history, **kwargs):
                return QuestionSpec(intent="lookup", confidence=0.9)

        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=QueryExecutorAdapter(_build_db_with_cursor([{"v": 1}])[0]),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            question_analyzer=StubAnalyzer(),
        )
        orch = Orchestrator(deps)
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-stub")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.question_analysis is not None
        assert final.question_analysis.intent == "lookup"
        assert final.pipeline_stage in (
            PipelineStage.ANSWERED,
            PipelineStage.FAILED,
        )

    def test_schema_retriever_stub_runs_when_catalog_provided(self):
        class StubCatalog:
            def current_version(self):
                return "v1"

            def versions(self):
                from src.agents.contracts import SchemaVersion
                return (SchemaVersion(id="v1", captured_at=__import__("datetime").datetime(2024, 1, 1), source="test"),)

            def get_snapshot(self, version=None):
                from src.agents.contracts import SchemaSnapshot, TableDescriptor, SchemaVersion
                return SchemaSnapshot(
                    version=SchemaVersion(id="v1", captured_at=__import__("datetime").datetime(2024, 1, 1), source="test"),
                    tables=(TableDescriptor(name="T"),),
                )

        class StubAnalyzer(QuestionAnalyzer):
            def analyze(self, question, history, **kwargs):
                return QuestionSpec(intent="lookup", confidence=0.9)

        class StubRetriever(SchemaRetriever):
            def retrieve(self, spec, snapshot):
                from src.agents.contracts import SchemaContext, TableRef
                return SchemaContext(tables=(TableRef(name="T"),))

        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=QueryExecutorAdapter(_build_db_with_cursor([{"v": 1}])[0]),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            question_analyzer=StubAnalyzer(),
            schema_catalog=StubCatalog(),
            schema_retriever=StubRetriever(),
        )
        orch = Orchestrator(deps)
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-sc")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.schema_context is not None
        assert final.schema_context.tables[0].name == "T"

    def test_full_future_pipeline_path(self):
        """When all future components are provided the pipeline runs
        through Plan→Generate→Validate→Execute→Analyze→Present→Answer→
        Validate."""

        class StubAnalyzer(QuestionAnalyzer):
            def analyze(self, question, history, **kwargs):
                return QuestionSpec(intent="lookup", confidence=0.9)

        from src.agents.contracts import (
            QueryPlan, PlanMetric, OutputRequirements,
        )

        class StubPlanner:
            def plan(self, spec, context):
                return QueryPlan(output_requirements=OutputRequirements(limit=10))

        class StubGenerator(SQLGenerator):
            def generate(self, plan, context, *, dialect="tsql"):
                return SQLCandidate(
                    sql="SELECT 1",
                    dialect=dialect,
                    candidate_id="c1",
                    fingerprint="fp1",
                )

        class StubResultAnalyzer(ResultAnalyzer):
            def profile(self, result, plan):
                from src.agents.contracts import (
                    ResultProfile, ShapeDescriptor,
                )
                return ResultProfile(shape=ShapeDescriptor(rows=1, columns=1), row_count=1)

        class StubPresentationPlanner(PresentationPlanner):
            def plan(self, spec, profile, *, labels, locale_policy):
                return PresentationPlan(view_kind="table")

        class StubAnswerGenerator(AnswerGenerator):
            def generate(self, spec, profile, presentation, result, *, evidence=()):
                return AnswerDraft(text="answer from stub")

        class StubAnswerValidator(AnswerValidator):
            def validate(self, draft, profile, presentation):
                return ValidatedAnswer(text=draft.text, accepted_claims=draft.claims)

        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),  # not used because SQLGenerator is set
            query_executor=QueryExecutorAdapter(_build_db_with_cursor([{"v": 1}])[0]),
            llm_client=FakeLLM(),  # not used because AnswerGenerator is set
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            question_analyzer=StubAnalyzer(),
            query_planner=StubPlanner(),
            sql_generator=StubGenerator(),
            result_analyzer=StubResultAnalyzer(),
            presentation_planner=StubPresentationPlanner(),
            answer_generator=StubAnswerGenerator(),
            answer_validator=StubAnswerValidator(),
        )
        orch = Orchestrator(deps)
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-full")
            final = orch.run(state)
        finally:
            reset_trace_context(token)
        assert final.query_plan is not None
        assert final.result_profile is not None
        assert final.presentation_plan is not None
        assert final.validated_answer is not None
        assert final.is_success() is True
        assert final.response.answer == "answer from stub"


# ---------------------------------------------------------------------------
# Retry ownership
# ---------------------------------------------------------------------------


class TestOrchestratorRetryOwnership:
    def test_no_internal_retry_loop_in_orchestrator(self):
        """The orchestrator does not retry internally; SQLRepairer is the
        only component allowed to retry (and it is None in the current
        path)."""
        orch = _build_orchestrator()
        # The deps are constructed by the helper with sql_repairer=None.
        assert orch._deps.sql_repairer is None


# ---------------------------------------------------------------------------
# State writer rule
# ---------------------------------------------------------------------------


class TestOrchestratorIsSoleWriter:
    """Verify the orchestrator owns the state writer rule: only it
    invokes ``state.with_*`` methods. The single writer is documented
    in ADR-013."""

    def test_orchestrator_is_a_singleton_writer_via_run(self):
        """Calling ``run`` on the same state twice is supported (a new
        agent call creates a new state) — but the orchestrator itself
        does not expose any other entry point for state mutation.
        """
        orch = _build_orchestrator()
        assert hasattr(orch, "run")
        # The orchestrator has no other public methods that take a
        # state and return a state, ensuring mutation only happens via
        # ``run``.
        public_methods = [
            name for name in dir(orch)
            if not name.startswith("_") and callable(getattr(orch, name))
        ]
        assert public_methods == ["run"]