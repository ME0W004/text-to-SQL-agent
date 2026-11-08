"""Orchestrator — single writer of ``AgentState``.

Sprint 2 Prompt 2B introduces the ``Orchestrator``. This class is the only
component allowed to mutate ``AgentState``. It receives typed inputs from
components (or falls back to the existing pipeline when future components
are not yet wired), applies results to state via the state-applier
methods, and transitions ``pipeline_stage`` accordingly.

Architectural rules (Prompt 2A / ADR-013):

* Only the Orchestrator writes ``AgentState``.
* Components never import ``AgentState`` or any sub-model.
* Retry policy is owned by the Orchestrator (Prompt 2A §7.6, ADR-013).
* Errors flow through the State error model — never raw dict paths.
* The Orchestrator is constructed with all dependencies via dependency
  injection. It MUST NOT instantiate infrastructure itself.
* The Orchestrator preserves the existing pipeline path
  (Sprint 2 Prompt 1A-1C) while being structurally ready for future
  components.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from time import perf_counter
from typing import Any, List, Optional, Sequence

from src.agents.contracts import (
    LLMRouter,
    MemoryManagerProtocol,
    QueryExecutor,
    QuestionAnalyzer,
    QueryPlanner,
    ResultAnalyzer,
    AnswerGenerator,
    AnswerValidator,
    AnswerDraft,
    SchemaCatalog,
    SchemaRetriever,
    SQLGenerator,
    SQLRepairer,
    SecurityValidator,
    SQLValidator as SQLValidatorProtocol,
    SemanticValidator,
    ComplexityValidator,
    PresentationPlanner,
    ValidatedAnswer,
    QueryResult,
    SchemaSnapshot,
    ResultProfile,
    PresentationPlan,
    QuestionSpec,
    SchemaContext,
    SchemaError,
    QueryPlan,
    SecurityDecision,
    LLMResult,
    LLMResult as _LLMResult,
)
from src.agents.state import (
    AgentState,
    ErrorCategory,
    ExecutionStatus,
    PipelineStage,
    ResponseState,
    ValidationStatus,
    create_initial_state,
    state_to_api_response,
)
from src.agents.adapters import (
    QueryExecutorAdapter,
    SecurityValidatorAdapter,
    default_answer_fn,
)
from src.core.database.connection import (
    DatabaseConnection,
    ExecutionPolicy,
)
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.sql_extractor.validator import SQLValidator as LegacySQLValidator
from src.core.security.sql_security import (
    SecurityDecision as CoreSecurityDecision,
    SQLSecurityError,
)
from src.utils.exceptions import QueryExecutionError
from src.utils.logger import (
    get_logger,
    log_event,
    reset_trace_context,
    set_trace_context,
    update_session_id,
)


logger = get_logger(__name__)


__all__ = ["Orchestrator", "OrchestratorDeps"]


# Type alias for the natural-language answer generation function.
# The Orchestrator does not know about prompt templates or provider
# details; the caller injects a callable that turns a (question, rows,
# row_count) tuple into an answer string. The transitional default is
# built by ``src.agents.adapters.default_answer_fn`` — the Orchestrator
# itself contains no prompt-construction logic.
AnswerFn = Any


class OrchestratorDeps:
    """Container of dependencies the Orchestrator needs.

    Every dependency is optional. When a future component Protocol is
    ``None`` the Orchestrator falls back to the existing implementation
    (Sprint 2 Prompt 1A-1C). This keeps the pipeline functional today
    while the new components are wired in later sprints.

    Required today (Sprint 2 Prompt 2B transitional Orchestrator):

    * ``sql_extractor`` — wraps LLM-driven SQL generation with retry.
    * ``query_executor`` — single database boundary.
    * ``llm_client`` — natural-language answer generation.
    * ``prompts`` — natural-response prompt builder.
    * ``memory_manager`` — session persistence.

    Optional / future:

    * ``question_analyzer``, ``schema_catalog``, ``schema_retriever``,
      ``query_planner``, ``sql_generator``, ``sql_validator``,
      ``security_validator``, ``semantic_validator``,
      ``complexity_validator``, ``sql_repairer``, ``result_analyzer``,
      ``presentation_planner``, ``answer_generator``, ``answer_validator``,
      ``llm_router``.
    """

    def __init__(
        self,
        *,
        sql_extractor: SQLExtractor,
        query_executor: QueryExecutor,
        llm_client: LLMClient,
        prompts: SystemPrompts,
        memory_manager: MemoryManagerProtocol,
        security_validator: Optional[SecurityValidator] = None,
        question_analyzer: Optional[QuestionAnalyzer] = None,
        schema_catalog: Optional[SchemaCatalog] = None,
        schema_retriever: Optional[SchemaRetriever] = None,
        query_planner: Optional[QueryPlanner] = None,
        sql_generator: Optional[SQLGenerator] = None,
        sql_validator: Optional[SQLValidatorProtocol] = None,
        semantic_validator: Optional[SemanticValidator] = None,
        complexity_validator: Optional[ComplexityValidator] = None,
        sql_repairer: Optional[SQLRepairer] = None,
        result_analyzer: Optional[ResultAnalyzer] = None,
        presentation_planner: Optional[PresentationPlanner] = None,
        answer_generator: Optional[AnswerGenerator] = None,
        answer_validator: Optional[AnswerValidator] = None,
        llm_router: Optional[LLMRouter] = None,
        execution_policy: Optional[ExecutionPolicy] = None,
        max_correction_attempts: int = 2,
        answer_fn: Optional[AnswerFn] = None,
    ) -> None:
        self.sql_extractor = sql_extractor
        self.query_executor = query_executor
        self.llm_client = llm_client
        self.prompts = prompts
        self.memory_manager = memory_manager
        self.security_validator = security_validator or SecurityValidatorAdapter()
        self.question_analyzer = question_analyzer
        self.schema_catalog = schema_catalog
        self.schema_retriever = schema_retriever
        self.query_planner = query_planner
        self.sql_generator = sql_generator
        self.sql_validator = sql_validator
        self.semantic_validator = semantic_validator
        self.complexity_validator = complexity_validator
        self.sql_repairer = sql_repairer
        self.result_analyzer = result_analyzer
        self.presentation_planner = presentation_planner
        self.answer_generator = answer_generator
        self.answer_validator = answer_validator
        self.llm_router = llm_router
        self.execution_policy = execution_policy or ExecutionPolicy.from_environment()
        self.max_correction_attempts = max_correction_attempts
        # ``answer_fn`` is the seam between the Orchestrator and the
        # current-path answer generation. It hides prompt template names
        # from the orchestrator source. If not provided, the transitional
        # default from ``src.agents.adapters`` is used (it wraps the
        # injected prompts/LLM client without the Orchestrator knowing
        # about prompt templates).
        self.answer_fn: AnswerFn = answer_fn or default_answer_fn(prompts, llm_client)


class Orchestrator:
    """The single writer of ``AgentState``.

    Construct with an :class:`OrchestratorDeps` instance. The
    Orchestrator receives an initial ``AgentState`` and returns the
    final ``AgentState`` after running the pipeline.
    """

    def __init__(self, deps: OrchestratorDeps) -> None:
        self._deps = deps

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run(self, state: AgentState) -> AgentState:
        """Run the full pipeline and return the final ``AgentState``.

        All transitions and state mutations happen here. Errors are
        routed through the state error model.
        """
        started = perf_counter()
        try:
            log_event(
                "orchestrator_started",
                component="orchestrator",
                request_id=state.request.request_id,
                trace_id=state.request.trace_id,
            )

            state = self._ensure_session(state)
            state = self._load_history(state)

            # Question analysis — currently a no-op stub.
            state = self._run_question_analysis(state)

            # Schema resolution — currently a no-op stub.
            state = self._run_schema_resolution(state)

            # Query planning — currently a no-op stub.
            state = self._run_query_planning(state)

            # SQL generation (current path uses SQLExtractor).
            state = self._run_sql_generation(state)

            # SQL validation (current path: SecurityValidatorAdapter).
            state = self._run_sql_validation(state)

            # If validation failed, SQLRepairer handles bounded retry.
            if state.sql.validation_status == ValidationStatus.FAILED and state.is_terminal() is False:
                state = self._run_sql_repair(state)

            # If still failing or security-rejected, transition to FAILED.
            if state.pipeline_stage != PipelineStage.FAILED and state.sql.is_validated is False:
                if state.pipeline_stage == PipelineStage.SQL_GENERATED:
                    # No further transitions; mark FAILED.
                    state = self._apply_validation_failure(
                        state, "SQL validation failed."
                    )

            # If SQL is valid, execute it.
            if state.sql.is_validated and not state.is_terminal():
                state = self._run_execution(state)

            # Result analysis / presentation / answer generation.
            if state.execution.is_success and not state.is_terminal():
                state = self._run_post_execution(state)

            self._persist_conversation(state)
            self._emit_completion(state, started)
            return state
        except (SQLSecurityError, QueryExecutionError, SchemaError, ValueError) as exc:
            return self._handle_known_failure(state, exc, started)
        except Exception as exc:  # pragma: no cover — defensive
            return self._handle_unknown_failure(state, exc, started)

    # ------------------------------------------------------------------
    # Session / memory helpers
    # ------------------------------------------------------------------

    def _ensure_session(self, state: AgentState) -> AgentState:
        session_id = state.request.session_id
        memory = self._deps.memory_manager
        if not session_id or not memory.session_exists(session_id):
            session_id = memory.create_session()
        update_session_id(session_id)
        return replace(state, request=state.request.with_session(session_id))

    def _load_history(self, state: AgentState) -> AgentState:
        history = self._deps.memory_manager.get_history(state.request.session_id, limit=5)
        return replace(state, conversation=state.conversation.with_history(history))

    def _persist_conversation(self, state: AgentState) -> None:
        if state.is_terminal() is False:
            return
        if state.response.success and state.response.answer:
            self._deps.memory_manager.add_message(
                state.request.session_id, "user", state.request.question
            )
            self._deps.memory_manager.add_message(
                state.request.session_id, "assistant", state.response.answer
            )

    # ------------------------------------------------------------------
    # Stage: Question analysis (stub for Sprint 2 Prompt 2B)
    # ------------------------------------------------------------------

    def _run_question_analysis(self, state: AgentState) -> AgentState:
        analyzer = self._deps.question_analyzer
        if analyzer is None:
            return state
        spec: QuestionSpec = analyzer.analyze(
            state.request.question,
            list(state.conversation.history),
            active_filters=state.conversation.active_filters or None,
            resolved_references=state.conversation.resolved_references or None,
        )
        return state.with_question_analysis(spec)

    # ------------------------------------------------------------------
    # Stage: Schema resolution (stub for Sprint 2 Prompt 2B)
    # ------------------------------------------------------------------

    def _run_schema_resolution(self, state: AgentState) -> AgentState:
        catalog = self._deps.schema_catalog
        retriever = self._deps.schema_retriever
        if catalog is None or retriever is None or state.question_analysis is None:
            # No schema components wired — apply an empty schema context
            # so subsequent stages can transition through SCHEMA_RESOLVED.
            if state.question_analysis is not None and state.schema_context is None:
                return state.with_schema_context(SchemaContext())
            return state
        from src.core.schema.retriever import serialized_context_size

        started = perf_counter()
        snapshot: SchemaSnapshot = catalog.get_snapshot()
        context: SchemaContext = retriever.retrieve(state.question_analysis, snapshot)
        state = state.with_schema_context(context)
        # Bounded schema-context metrics (Phase 3.2): counts only, recorded
        # by the Orchestrator — the sole writer of AgentState. The seam is
        # dormant on the live path until a QuestionAnalyzer supplies a
        # QuestionSpec, so these metrics stay empty on real requests.
        state = state.with_stage_timing(
            "schema_resolution", (perf_counter() - started) * 1000
        )
        return state.with_schema_metrics(
            {
                "selected_tables": len(context.tables),
                "selected_columns": len(context.columns),
                "join_hints": len(context.joins),
                "semantics_entries": len(context.relevant_business_semantics),
                "context_chars": serialized_context_size(context),
            }
        )

    # ------------------------------------------------------------------
    # Stage: Query planning (stub for Sprint 2 Prompt 2B)
    # ------------------------------------------------------------------

    def _run_query_planning(self, state: AgentState) -> AgentState:
        planner = self._deps.query_planner
        if planner is None or state.question_analysis is None:
            return state
        context = state.schema_context
        if context is None:
            # Provide an empty schema context so a planner can run without
            # a SchemaRetriever (transitional mode).
            context = SchemaContext()
        plan: QueryPlan = planner.plan(state.question_analysis, context)
        return state.with_query_plan(plan)

    # ------------------------------------------------------------------
    # Stage: SQL generation
    # ------------------------------------------------------------------

    def _run_sql_generation(self, state: AgentState) -> AgentState:
        generator = self._deps.sql_generator
        if generator is None:
            sql = self._deps.sql_extractor.extract(
                state.request.question, history=list(state.conversation.history)
            )
            return state.with_generated_sql(sql)
        if state.query_plan is None:
            return state
        from src.utils.logger import sql_fingerprint  # local import keeps contracts clean
        context = state.schema_context
        if context is None:
            context = SchemaContext()
        candidate = generator.generate(state.query_plan, context)
        # Apply SQL with fingerprint regardless of generator source.
        return replace(
            state,
            sql=state.sql.with_sql(candidate.sql).with_security_decision(
                self._deps.security_validator.validate(candidate.sql)
            ),
        ).transition_to(PipelineStage.SQL_GENERATED)

    # ------------------------------------------------------------------
    # Stage: SQL validation
    # ------------------------------------------------------------------

    def _run_sql_validation(self, state: AgentState) -> AgentState:
        if state.sql.generated_sql is None:
            return state
        decision: SecurityDecision = self._deps.security_validator.validate(
            state.sql.generated_sql
        )
        sql_with_decision = state.sql.with_security_decision(decision)
        if not decision.valid:
            reason = decision.reason_code.value if decision.reason_code else "SECURITY_REJECTED"
            message = decision.message or "SQL rejected by security validator."
            return replace(
                state, sql=sql_with_decision
            ).with_security_rejected(reason, message)
        return replace(state, sql=sql_with_decision).with_validation_passed()

    # ------------------------------------------------------------------
    # Stage: SQL repair (bounded retry owned by Orchestrator)
    # ------------------------------------------------------------------

    def _run_sql_repair(self, state: AgentState) -> AgentState:
        repairer = self._deps.sql_repairer
        if repairer is None:
            return state
        # Repairer integration is documented but not yet implemented in this
        # Sprint; future Sprints will wire it. The current pipeline has
        # already attempted bounded retry inside SQLExtractor; if we are
        # here it means the SQL still failed, so we surface the failure.
        return state

    # ------------------------------------------------------------------
    # Helpers for validation failure
    # ------------------------------------------------------------------

    def _apply_validation_failure(self, state: AgentState, message: str) -> AgentState:
        return state.with_validation_failed(message, reason_code="VALIDATION_ERROR")

    # ------------------------------------------------------------------
    # Stage: Execution
    # ------------------------------------------------------------------

    def _run_execution(self, state: AgentState) -> AgentState:
        sql = state.sql.generated_sql
        if sql is None:
            return state
        try:
            result: QueryResult = self._deps.query_executor.execute(
                sql, policy=self._deps.execution_policy
            )
        except QueryExecutionError as exc:
            db_error_code = getattr(getattr(exc, "code", None), "value", None)
            return state.with_execution_error(
                status=ExecutionStatus.FAILED,
                error_code=db_error_code or "DATABASE_QUERY_ERROR",
                error_message=str(exc),
                database_error_code=db_error_code,
            )
        rows = [dict(row) for row in result.rows]
        return state.with_execution_results(rows, result.duration_ms)

    # ------------------------------------------------------------------
    # Stage: Post-execution (result/profile/answer)
    # ------------------------------------------------------------------

    def _run_post_execution(self, state: AgentState) -> AgentState:
        # Result analysis (deterministic)
        if self._deps.result_analyzer is not None and state.query_plan is not None:
            result = self._result_from_state(state)
            profile: ResultProfile = self._deps.result_analyzer.profile(result, state.query_plan)
            state = state.with_result_profile(profile)
        # Presentation planning (deterministic)
        if (
            self._deps.presentation_planner is not None
            and state.question_analysis is not None
            and state.result_profile is not None
        ):
            labels: dict = {}
            presentation: PresentationPlan = self._deps.presentation_planner.plan(
                state.question_analysis,
                state.result_profile,
                labels=labels,
                locale_policy=_default_locale_policy(),
            )
            state = state.with_presentation_plan(presentation)
        # Answer generation
        if (
            self._deps.answer_generator is not None
            and state.question_analysis is not None
            and state.result_profile is not None
            and state.presentation_plan is not None
        ):
            result = self._result_from_state(state)
            draft: AnswerDraft = self._deps.answer_generator.generate(
                state.question_analysis,
                state.result_profile,
                state.presentation_plan,
                result,
            )
            # Answer validation
            if self._deps.answer_validator is not None:
                validated: ValidatedAnswer = self._deps.answer_validator.validate(
                    draft, state.result_profile, state.presentation_plan
                )
                state = state.with_validated_answer(validated)
                return state
            # No validator — accept the draft directly.
            from src.agents.contracts import ValidatedAnswer as _VA
            state = state.with_validated_answer(
                _VA(text=draft.text, accepted_claims=draft.claims)
            )
            return state

        # Current path (no future components wired): natural-response LLM call.
        return self._run_current_answer(state)

    # ------------------------------------------------------------------
    # Current path: natural response generation
    # ------------------------------------------------------------------

    def _run_current_answer(self, state: AgentState) -> AgentState:
        rows = [dict(row) for row in state.execution.rows]
        if not rows:
            return state.with_answer("No data found for your query.")
        llm_started = perf_counter()
        try:
            log_event(
                "llm_call_started",
                component="llm",
                purpose="response_generation",
                trace_id=state.request.trace_id,
            )
            try:
                answer = self._deps.answer_fn(
                    state.request.question, rows, len(rows)
                )
            except Exception as exc:
                log_event(
                    "llm_call_failed",
                    level=logging.ERROR,
                    component="llm",
                    purpose="response_generation",
                    error_type=type(exc).__name__,
                    duration_ms=round((perf_counter() - llm_started) * 1000, 3),
                    trace_id=state.request.trace_id,
                )
                raise
            llm_duration = (perf_counter() - llm_started) * 1000
            state = state.with_llm_call(llm_duration)
            log_event(
                "llm_call_completed",
                component="llm",
                purpose="response_generation",
                duration_ms=round(llm_duration, 3),
                trace_id=state.request.trace_id,
            )
            return state.with_answer(answer)
        except Exception as exc:
            log_event(
                "llm_call_failed",
                level=logging.ERROR,
                component="llm",
                purpose="response_generation",
                error_type=type(exc).__name__,
                trace_id=state.request.trace_id,
            )
            return state.with_error_response(
                error="LLM unavailable.",
                category=ErrorCategory.LLM,
                code=getattr(exc, "code", "ANS.LLM_UNAVAILABLE"),
                stage=state.pipeline_stage,
                retryable=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result_from_state(state: AgentState) -> QueryResult:
        return QueryResult(
            columns=tuple(state.execution.columns),
            rows=tuple(state.execution.rows),
            row_count=state.execution.row_count,
            truncated=state.execution.truncated,
            duration_ms=state.execution.duration_ms or 0.0,
        )

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _handle_known_failure(
        self, state: AgentState, exc: Exception, started: float
    ) -> AgentState:
        if isinstance(exc, SQLSecurityError):
            reason = exc.code.value if exc.code else "SECURITY_REJECTED"
            message = f"Invalid SQL: {exc.message}"
            new_state = state.with_security_rejected(reason, message)
        elif isinstance(exc, QueryExecutionError):
            db_code = getattr(getattr(exc, "code", None), "value", None)
            new_state = state.with_execution_error(
                status=ExecutionStatus.FAILED,
                error_code=db_code or "DATABASE_QUERY_ERROR",
                error_message=str(exc),
                database_error_code=db_code,
            )
        elif isinstance(exc, SchemaError):
            # Typed schema failure (e.g. SCH.UNAVAILABLE when no snapshot
            # exists and none can be built) — keep the approved code.
            new_state = state.with_error_response(
                error=exc.user_message,
                category=ErrorCategory.SCHEMA,
                code=exc.code,
                stage=state.pipeline_stage,
                retryable=False,
            )
        else:  # ValueError etc.
            new_state = state.with_error_response(
                error=str(exc) or "Internal error.",
                category=ErrorCategory.INTERNAL,
                code=type(exc).__name__,
                stage=state.pipeline_stage,
                retryable=False,
            )
        self._emit_failure(new_state, started, exc)
        return new_state

    def _handle_unknown_failure(
        self, state: AgentState, exc: Exception, started: float
    ) -> AgentState:
        new_state = state.with_error_response(
            error="Internal error.",
            category=ErrorCategory.INTERNAL,
            code=type(exc).__name__,
            stage=state.pipeline_stage,
            retryable=False,
        )
        self._emit_failure(new_state, started, exc)
        return new_state

    def _emit_completion(self, state: AgentState, started: float) -> None:
        log_event(
            "orchestrator_completed",
            component="orchestrator",
            pipeline_stage=state.pipeline_stage.value,
            success=state.is_success(),
            duration_ms=round((perf_counter() - started) * 1000, 3),
            trace_id=state.request.trace_id,
        )

    def _emit_failure(self, state: AgentState, started: float, exc: Exception) -> None:
        log_event(
            "orchestrator_failed",
            level=logging.ERROR,
            component="orchestrator",
            error_type=type(exc).__name__,
            pipeline_stage=state.pipeline_stage.value,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            trace_id=state.request.trace_id,
        )


def _default_locale_policy():
    from src.agents.contracts import LocalePolicy
    return LocalePolicy()