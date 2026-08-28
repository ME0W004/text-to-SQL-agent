"""Agent State Model - Central typed state for the Text-to-SQL pipeline.

This module defines the AgentState and supporting models that carry data through
the pipeline stages. It is a pure data/state model - no business logic, no
external dependencies, no infrastructure resources.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from src.utils.logger import sql_fingerprint


class PipelineStage(str, Enum):
    """Explicit pipeline stages for predictable transitions."""
    INITIALIZED = "initialized"
    QUESTION_ANALYZED = "question_analyzed"
    SCHEMA_RESOLVED = "schema_resolved"
    PLAN_CREATED = "plan_created"
    SQL_GENERATED = "sql_generated"
    VALIDATED = "validated"
    EXECUTED = "executed"
    RESULT_ANALYZED = "result_analyzed"
    ANSWERED = "answered"
    FAILED = "failed"


class ErrorCategory(str, Enum):
    """Categorized error types for handling decisions."""
    QUESTION = "question"
    SCHEMA = "schema"
    PLANNING = "planning"
    SQL_GENERATION = "sql_generation"
    SECURITY = "security"
    VALIDATION = "validation"
    EXECUTION = "execution"
    RESPONSE = "response"
    INTERNAL = "internal"


class ValidationStatus(str, Enum):
    """SQL validation status."""
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SECURITY_REJECTED = "security_rejected"


class ExecutionStatus(str, Enum):
    """Database execution status."""
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    ROW_LIMIT_EXCEEDED = "row_limit_exceeded"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class RequestContext:
    """Immutable request identity - separated from runtime state."""
    request_id: str = field(default_factory=lambda: uuid4().hex)
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    session_id: Optional[str] = None
    question: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def with_session(self, session_id: str) -> RequestContext:
        return replace(self, session_id=session_id)

    def with_trace(self, trace_id: str) -> RequestContext:
        return replace(self, trace_id=trace_id)


@dataclass(frozen=True)
class ConversationContext:
    """Conversation context for reference resolution - no storage dependencies."""
    history: tuple[dict[str, str], ...] = ()
    resolved_references: dict[str, Any] = field(default_factory=dict)
    active_filters: dict[str, Any] = field(default_factory=dict)

    def with_history(self, history: list[dict[str, str]]) -> ConversationContext:
        return replace(self, history=tuple(history))

    def with_reference(self, key: str, value: Any) -> ConversationContext:
        new_refs = dict(self.resolved_references)
        new_refs[key] = value
        return replace(self, resolved_references=new_refs)


@dataclass(frozen=True)
class SQLState:
    """SQL-related state - data only, no validators."""
    generated_sql: Optional[str] = None
    sql_fingerprint: Optional[str] = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    validation_message: Optional[str] = None
    validation_reason_code: Optional[str] = None
    correction_attempt: int = 0
    max_correction_attempts: int = 2

    def with_sql(self, sql: str) -> SQLState:
        return replace(
            self,
            generated_sql=sql,
            sql_fingerprint=sql_fingerprint(sql),
            validation_status=ValidationStatus.PENDING,
            validation_message=None,
            validation_reason_code=None,
        )

    def with_validation_result(
        self,
        status: ValidationStatus,
        message: Optional[str] = None,
        reason_code: Optional[str] = None,
    ) -> SQLState:
        return replace(
            self,
            validation_status=status,
            validation_message=message,
            validation_reason_code=reason_code,
        )

    def with_correction_attempt(self) -> SQLState:
        return replace(self, correction_attempt=self.correction_attempt + 1)

    @property
    def can_retry_correction(self) -> bool:
        return self.correction_attempt < self.max_correction_attempts

    @property
    def is_validated(self) -> bool:
        return self.validation_status == ValidationStatus.PASSED

    @property
    def is_security_rejected(self) -> bool:
        return self.validation_status == ValidationStatus.SECURITY_REJECTED


@dataclass(frozen=True)
class ExecutionState:
    """Database execution results - no connection/cursor resources."""
    status: ExecutionStatus = ExecutionStatus.PENDING
    rows: tuple[dict[str, Any], ...] = ()
    row_count: int = 0
    duration_ms: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    database_error_code: Optional[str] = None

    def with_results(self, rows: list[dict[str, Any]], duration_ms: float) -> ExecutionState:
        return replace(
            self,
            status=ExecutionStatus.SUCCESS,
            rows=tuple(rows),
            row_count=len(rows),
            duration_ms=duration_ms,
        )

    def with_error(
        self,
        status: ExecutionStatus,
        error_code: str,
        error_message: str,
        database_error_code: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> ExecutionState:
        return replace(
            self,
            status=status,
            error_code=error_code,
            error_message=error_message,
            database_error_code=database_error_code,
            duration_ms=duration_ms,
        )

    @property
    def has_data(self) -> bool:
        return self.row_count > 0

    @property
    def is_success(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS


@dataclass(frozen=True)
class ResponseState:
    """Final response state - presentation agnostic."""
    answer: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_success(self, answer: str, metadata: Optional[dict[str, Any]] = None) -> ResponseState:
        return replace(
            self,
            answer=answer,
            success=True,
            error=None,
            metadata=metadata or {},
        )

    def with_error(self, error: str, metadata: Optional[dict[str, Any]] = None) -> ResponseState:
        return replace(
            self,
            answer=None,
            success=False,
            error=error,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class ErrorState:
    """Structured error information - no raw exception objects."""
    category: Optional[ErrorCategory] = None
    code: Optional[str] = None
    message: Optional[str] = None
    stage: Optional[PipelineStage] = None
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        stage: PipelineStage,
        category: ErrorCategory,
        retryable: bool = False,
    ) -> ErrorState:
        return cls(
            category=category,
            code=type(exc).__name__,
            message=str(exc),
            stage=stage,
            retryable=retryable,
        )

    @classmethod
    def security_error(
        cls,
        reason_code: str,
        message: str,
        stage: PipelineStage = PipelineStage.VALIDATED,
    ) -> ErrorState:
        return cls(
            category=ErrorCategory.SECURITY,
            code=reason_code,
            message=message,
            stage=stage,
            retryable=False,
        )

    @classmethod
    def execution_error(
        cls,
        error_code: str,
        message: str,
        database_error_code: Optional[str] = None,
        stage: PipelineStage = PipelineStage.EXECUTED,
        retryable: bool = True,
    ) -> ErrorState:
        return cls(
            category=ErrorCategory.EXECUTION,
            code=error_code,
            message=message,
            stage=stage,
            retryable=retryable,
            details={"database_error_code": database_error_code} if database_error_code else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value if self.category else None,
            "code": self.code,
            "message": self.message,
            "stage": self.stage.value if self.stage else None,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True)
class RuntimeMetadata:
    """Observability metadata - separated from business state."""
    stage_timings: dict[str, float] = field(default_factory=dict)
    stage_start_times: dict[str, float] = field(default_factory=dict)
    llm_calls: int = 0
    llm_total_duration_ms: float = 0.0
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    attempt_counters: dict[str, int] = field(default_factory=dict)

    def with_stage_start(self, stage: str, timestamp: float) -> RuntimeMetadata:
        new_starts = dict(self.stage_start_times)
        new_starts[stage] = timestamp
        return replace(self, stage_start_times=new_starts)

    def with_stage_end(self, stage: str, start_timestamp: float, end_timestamp: float) -> RuntimeMetadata:
        new_timings = dict(self.stage_timings)
        new_timings[stage] = end_timestamp - start_timestamp
        new_starts = dict(self.stage_start_times)
        new_starts.pop(stage, None)
        return replace(self, stage_timings=new_timings, stage_start_times=new_starts)

    def with_llm_call(self, duration_ms: float, provider: Optional[str] = None, model: Optional[str] = None) -> RuntimeMetadata:
        return replace(
            self,
            llm_calls=self.llm_calls + 1,
            llm_total_duration_ms=self.llm_total_duration_ms + duration_ms,
            model_provider=provider or self.model_provider,
            model_name=model or self.model_name,
        )

    def increment_attempt(self, operation: str) -> RuntimeMetadata:
        new_counters = dict(self.attempt_counters)
        new_counters[operation] = new_counters.get(operation, 0) + 1
        return replace(self, attempt_counters=new_counters)


# Valid stage transitions for predictability
VALID_TRANSITIONS: dict[PipelineStage, set[PipelineStage]] = {
    PipelineStage.INITIALIZED: {
        PipelineStage.QUESTION_ANALYZED,
        PipelineStage.SQL_GENERATED,  # Direct path for current architecture
        PipelineStage.FAILED,
    },
    PipelineStage.QUESTION_ANALYZED: {
        PipelineStage.SCHEMA_RESOLVED,
        PipelineStage.FAILED,
    },
    PipelineStage.SCHEMA_RESOLVED: {
        PipelineStage.PLAN_CREATED,
        PipelineStage.FAILED,
    },
    PipelineStage.PLAN_CREATED: {
        PipelineStage.SQL_GENERATED,
        PipelineStage.FAILED,
    },
    PipelineStage.SQL_GENERATED: {
        PipelineStage.VALIDATED,
        PipelineStage.FAILED,
    },
    PipelineStage.VALIDATED: {
        PipelineStage.EXECUTED,
        PipelineStage.FAILED,
    },
    PipelineStage.EXECUTED: {
        PipelineStage.RESULT_ANALYZED,
        PipelineStage.ANSWERED,  # Direct path for current architecture
        PipelineStage.FAILED,
    },
    PipelineStage.RESULT_ANALYZED: {
        PipelineStage.ANSWERED,
        PipelineStage.FAILED,
    },
    PipelineStage.ANSWERED: set(),  # Terminal
    PipelineStage.FAILED: set(),  # Terminal
}


@dataclass
class AgentState:
    """
    Central agent state model - carries data through pipeline stages.

    This is a DATA MODEL only. It does not:
    - Generate SQL
    - Validate SQL
    - Call databases
    - Call LLMs
    - Build prompts
    - Manage memory
    - Analyze results
    - Render presentations

    It only holds the status and data needed by pipeline stages.
    """

    # Immutable request identity
    request: RequestContext

    # Conversation context (reference resolution, not storage)
    conversation: ConversationContext = field(default_factory=ConversationContext)

    # Pipeline control
    pipeline_stage: PipelineStage = PipelineStage.INITIALIZED

    # Stage data (typed placeholders for future components)
    question_analysis: Optional[Any] = None  # QuestionSpec in future
    schema_context: Optional[Any] = None     # SchemaContext in future
    query_plan: Optional[Any] = None         # QueryPlan in future
    result_profile: Optional[Any] = None     # ResultProfile in future
    presentation_plan: Optional[Any] = None  # PresentationPlan in future

    # Core stage states
    sql: SQLState = field(default_factory=SQLState)
    execution: ExecutionState = field(default_factory=ExecutionState)
    response: ResponseState = field(default_factory=ResponseState)
    error: ErrorState = field(default_factory=ErrorState)
    metadata: RuntimeMetadata = field(default_factory=RuntimeMetadata)

    def __post_init__(self):
        # Ensure frozen sub-objects are properly initialized
        pass

    # --- Pipeline Stage Transitions ---

    def transition_to(self, new_stage: PipelineStage) -> AgentState:
        """Transition to a new pipeline stage with validation."""
        if new_stage not in VALID_TRANSITIONS.get(self.pipeline_stage, set()):
            raise ValueError(
                f"Invalid transition: {self.pipeline_stage.value} -> {new_stage.value}"
            )
        return replace(self, pipeline_stage=new_stage)

    def can_transition_to(self, new_stage: PipelineStage) -> bool:
        return new_stage in VALID_TRANSITIONS.get(self.pipeline_stage, set())

    # --- Convenience Methods for Current Architecture Integration ---

    def with_generated_sql(self, sql: str) -> AgentState:
        """Record generated SQL and transition to SQL_GENERATED."""
        new_sql = self.sql.with_sql(sql)
        return replace(self, sql=new_sql).transition_to(PipelineStage.SQL_GENERATED)

    def with_validation_passed(self) -> AgentState:
        """Mark SQL as validated and transition to VALIDATED."""
        new_sql = self.sql.with_validation_result(ValidationStatus.PASSED)
        return replace(self, sql=new_sql).transition_to(PipelineStage.VALIDATED)

    def with_validation_failed(
        self,
        message: str,
        reason_code: Optional[str] = None,
    ) -> AgentState:
        """Mark SQL validation as failed."""
        new_sql = self.sql.with_validation_result(
            ValidationStatus.FAILED,
            message=message,
            reason_code=reason_code,
        )
        new_error = ErrorState(
            category=ErrorCategory.VALIDATION,
            code=reason_code or "VALIDATION_ERROR",
            message=message,
            stage=PipelineStage.VALIDATED,
            retryable=self.sql.can_retry_correction,
        )
        return replace(self, sql=new_sql, error=new_error)

    def with_security_rejected(
        self,
        reason_code: str,
        message: str,
    ) -> AgentState:
        """Mark SQL as security rejected."""
        new_sql = self.sql.with_validation_result(
            ValidationStatus.SECURITY_REJECTED,
            message=message,
            reason_code=reason_code,
        )
        new_error = ErrorState.security_error(reason_code, message)
        return replace(self, sql=new_sql, error=new_error).transition_to(PipelineStage.FAILED)

    def with_correction_attempt(self, sql: str) -> AgentState:
        """Record a correction attempt with new SQL."""
        new_sql = self.sql.with_correction_attempt().with_sql(sql)
        return replace(self, sql=new_sql)

    def with_execution_results(
        self,
        rows: list[dict[str, Any]],
        duration_ms: float,
    ) -> AgentState:
        """Record successful execution results."""
        new_exec = self.execution.with_results(rows, duration_ms)
        return replace(self, execution=new_exec).transition_to(PipelineStage.EXECUTED)

    def with_execution_error(
        self,
        status: ExecutionStatus,
        error_code: str,
        error_message: str,
        database_error_code: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> AgentState:
        """Record execution error."""
        new_exec = self.execution.with_error(
            status, error_code, error_message, database_error_code, duration_ms
        )
        new_error = ErrorState.execution_error(
            error_code, error_message, database_error_code
        )
        return replace(
            self, execution=new_exec, error=new_error
        ).transition_to(PipelineStage.FAILED)

    def with_answer(self, answer: str, metadata: Optional[dict[str, Any]] = None) -> AgentState:
        """Record final answer."""
        new_response = self.response.with_success(answer, metadata)
        return replace(self, response=new_response).transition_to(PipelineStage.ANSWERED)

    def with_error_response(
        self,
        error: str,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        code: Optional[str] = None,
        stage: Optional[PipelineStage] = None,
        retryable: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> AgentState:
        """Record error response."""
        new_response = self.response.with_error(error, metadata)
        new_error = ErrorState(
            category=category,
            code=code or "INTERNAL_ERROR",
            message=error,
            stage=stage or self.pipeline_stage,
            retryable=retryable,
        )
        return replace(
            self, response=new_response, error=new_error
        ).transition_to(PipelineStage.FAILED)

    # --- Metadata / Timing ---

    def with_stage_timing(self, stage: str, duration_ms: float) -> AgentState:
        """Record stage timing."""
        new_metadata = self.metadata.with_stage_end(stage, 0, duration_ms)  # simplified
        return replace(self, metadata=new_metadata)

    def with_llm_call(self, duration_ms: float, provider: Optional[str] = None, model: Optional[str] = None) -> AgentState:
        """Record LLM call metadata."""
        new_metadata = self.metadata.with_llm_call(duration_ms, provider, model)
        return replace(self, metadata=new_metadata)

    def increment_attempt(self, operation: str) -> AgentState:
        """Increment attempt counter for an operation."""
        new_metadata = self.metadata.increment_attempt(operation)
        return replace(self, metadata=new_metadata)

    # --- Serialization / Safe Representation ---

    def to_dict(self, *, include_sensitive: bool = False) -> dict[str, Any]:
        """Serialize to dictionary with optional sensitive data redaction."""
        data = {
            "request": {
                "request_id": self.request.request_id,
                "trace_id": self.request.trace_id,
                "session_id": self.request.session_id,
                "question": self.request.question if include_sensitive else "[REDACTED]",
                "timestamp": self.request.timestamp.isoformat(),
            },
            "conversation": {
                "history_length": len(self.conversation.history),
                "has_resolved_references": bool(self.conversation.resolved_references),
                "has_active_filters": bool(self.conversation.active_filters),
            },
            "pipeline_stage": self.pipeline_stage.value,
            "sql": {
                "has_sql": self.sql.generated_sql is not None,
                "sql_fingerprint": self.sql.sql_fingerprint,
                "validation_status": self.sql.validation_status.value,
                "validation_message": self.sql.validation_message,
                "validation_reason_code": self.sql.validation_reason_code,
                "correction_attempt": self.sql.correction_attempt,
            },
            "execution": {
                "status": self.execution.status.value,
                "row_count": self.execution.row_count,
                "duration_ms": self.execution.duration_ms,
                "error_code": self.execution.error_code,
                "error_message": self.execution.error_message,
                "database_error_code": self.execution.database_error_code,
            },
            "response": {
                "success": self.response.success,
                "has_answer": self.response.answer is not None,
                "error": self.response.error,
                "metadata_keys": list(self.response.metadata.keys()),
            },
            "error": self.error.to_dict() if self.error.category else None,
            "metadata": {
                "stage_timings": self.metadata.stage_timings,
                "llm_calls": self.metadata.llm_calls,
                "llm_total_duration_ms": self.metadata.llm_total_duration_ms,
                "model_provider": self.metadata.model_provider,
                "model_name": self.metadata.model_name,
                "attempt_counters": self.metadata.attempt_counters,
            },
        }
        if include_sensitive:
            data["request"]["question"] = self.request.question
            data["sql"]["generated_sql"] = self.sql.generated_sql
            data["execution"]["rows"] = [
                {k: v for k, v in row.items()} for row in self.execution.rows
            ]
            data["response"]["answer"] = self.response.answer
        return data

    def safe_repr(self) -> str:
        """Safe representation for logging - no sensitive data."""
        return f"AgentStage(request_id={self.request.request_id[:8]}, stage={self.pipeline_stage.value}, success={self.response.success})"

    def __repr__(self) -> str:
        return self.safe_repr()

    def __str__(self) -> str:
        return self.safe_repr()

    # --- State Invariants ---

    def validate_invariants(self) -> list[str]:
        """Check state invariants. Returns list of violations (empty if valid)."""
        violations = []

        if self.execution.is_success and self.execution.row_count == 0 and not self.execution.rows:
            # Empty result is valid
            pass

        if self.execution.status == ExecutionStatus.SUCCESS and self.execution.row_count > 0:
            if not self.execution.rows:
                violations.append("Execution success but rows missing")

        if self.sql.is_security_rejected:
            if self.execution.status != ExecutionStatus.PENDING:
                violations.append("Security rejected but execution already ran")

        if self.response.success and not self.response.answer:
            violations.append("Response success but no answer")

        if not self.response.success and not self.response.error:
            violations.append("Response failed but no error message")

        if self.pipeline_stage == PipelineStage.FAILED and not self.error.category:
            violations.append("Pipeline failed but no error category")

        if self.sql.validation_status == ValidationStatus.PASSED and not self.sql.generated_sql:
            violations.append("Validation passed but no SQL")

        return violations

    def is_terminal(self) -> bool:
        """Check if pipeline reached a terminal stage."""
        return self.pipeline_stage in (PipelineStage.ANSWERED, PipelineStage.FAILED)

    def is_failed(self) -> bool:
        return self.pipeline_stage == PipelineStage.FAILED

    def is_success(self) -> bool:
        return self.pipeline_stage == PipelineStage.ANSWERED and self.response.success


def create_initial_state(
    question: str,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    history: Optional[list[dict[str, str]]] = None,
) -> AgentState:
    """Factory for creating initial agent state."""
    request = RequestContext(
        question=question,
        session_id=session_id,
        trace_id=trace_id or uuid4().hex,
    )
    conversation = ConversationContext()
    if history:
        conversation = conversation.with_history(history)

    return AgentState(request=request, conversation=conversation)


def state_to_api_response(state: AgentState) -> dict[str, Any]:
    """Convert AgentState to API response format (backward compatible)."""
    if state.is_success():
        return {
            "success": True,
            "question": state.request.question,
            "answer": state.response.answer,
            "sql": state.sql.generated_sql,
            "row_count": state.execution.row_count,
            "rows": list(state.execution.rows),
            "session_id": state.request.session_id,
        }
    else:
        result = {
            "success": False,
            "question": state.request.question,
            "error": state.response.error or state.error.message or "Unknown error",
            "sql": state.sql.generated_sql,
            "session_id": state.request.session_id,
        }
        if state.execution.database_error_code:
            result["database_error_code"] = state.execution.database_error_code
        return result