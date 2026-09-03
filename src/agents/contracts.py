"""Target Architecture Contracts — typed value objects, interfaces, and errors.

Sprint 2 Prompt 2B introduces the architectural plumbing defined in
Prompt 2A. This module is the canonical location for:

* **Typed value objects** that flow between components
  (QuestionSpec, QueryPlan, SQLCandidate, QueryResult, ...).
* **Component interfaces** (Protocols) for the 17 target components.
* **Typed error hierarchy** for new component errors and the error
  code registry defined by ADR-015.

This module depends on:

* The Security Core (`src/core/security/sql_security.py`) — for the
  authoritative `SecurityDecision` and `SecurityPolicy`.
* The execution policy types (`src/core/database/policy.py`) — for
  `ExecutionPolicy` and `DatabaseErrorCode`. That module is
  driver-free; the DB driver adapter (`connection.py`) is imported
  only by the adapters layer, never by contracts.
* The observability surface (`src/utils/logger.py`) — components import
  only this for logging.

It MUST NOT import:

* `src.agents.state` — `AgentState` is the orchestrator's concern.
* `pymssql`, `sqlite3`, provider SDKs.
* `MemoryManager` directly.
* `fastapi`, `pydantic`, `src.api.*`.

These rules are enforced by tests under
`tests/test_architecture/` (`test_dependencies.py` and
`test_import_footprint.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from src.core.security.sql_security import (
    DEFAULT_SECURITY_POLICY,
    SecurityDecision,
    SecurityPolicy,
)
from src.core.database.policy import (
    DatabaseErrorCode,
    ExecutionPolicy,
)


__all__ = [
    # === Question domain ===
    "Entity", "QuestionFilter", "QuestionMetric", "TimeConstraint",
    "Reference", "AmbiguityReport", "QuestionSpec",
    # === Schema domain ===
    "TableDescriptor", "Relationship", "BusinessSemantics",
    "SchemaVersion", "SchemaSnapshot",
    "TableRef", "ColumnRef", "JoinHint", "SchemaContext",
    # === Planning domain ===
    "Join", "Dimension", "PlanMetric", "PlanFilter", "SortKey",
    "OutputRequirements", "QueryPlan",
    # === SQL generation/validation ===
    "SQLCandidate", "ParsedAST", "SyntaxError", "ValidationReport",
    "SemanticViolation", "SemanticReport",
    "ComplexityBudget", "ComplexityReport",
    # === Execution ===
    "QueryResult",
    # === Result/Presentation ===
    "ColumnType", "ShapeDescriptor", "AggregateRef", "ResultProfile",
    "FormatSpec", "LocalePolicy", "PresentationPlan",
    # === Answer ===
    "Claim", "Evidence", "AnswerDraft", "ValidatedAnswer",
    # === LLM ===
    "Usage", "LLMPolicy", "LLMBudget", "LLMResult",
    # === Errors ===
    "ErrorStageTag", "TypedComponentError",
    "QuestionAnalysisError", "SchemaError", "PlanningError",
    "SQLGenerationError", "RepairError", "ResultAnalysisError",
    "PresentationError", "AnswerGenerationError", "AnswerValidationError",
    "MemoryError", "LLMRouterError",
    # === Interfaces ===
    "QuestionAnalyzer", "SchemaCatalog", "SchemaRetriever",
    "QueryPlanner", "SQLGenerator", "SQLValidator", "SecurityValidator",
    "SemanticValidator", "ComplexityValidator", "SQLRepairer",
    "QueryExecutor", "ResultAnalyzer", "PresentationPlanner",
    "AnswerGenerator", "AnswerValidator", "MemoryManagerProtocol",
    "LLMRouter",
]


# ---------------------------------------------------------------------------
# Question domain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entity:
    """A typed mention span identified by QuestionAnalyzer."""

    text: str
    entity_type: str  # "table" | "column" | "metric" | "entity" | ...
    qualified_name: Optional[str] = None  # e.g. "Sale.Orders"
    confidence: float = 1.0


@dataclass(frozen=True)
class QuestionFilter:
    """Filter intent expressed in user question terms.

    Distinct from `PlanFilter` (which is the planning-time SQL-aware
    filter). This value object is the *intent*; `PlanFilter` is the
    realisation.
    """

    field: str
    operator: str  # "eq" | "ne" | "in" | "between" | "like" | "gt" | "lt"
    value: Any
    confidence: float = 1.0


@dataclass(frozen=True)
class QuestionMetric:
    """Metric intent in the user's question (e.g. "revenue").

    Distinct from `PlanMetric` (which carries aggregation function and
    output alias).
    """

    name: str
    aggregation: Optional[str] = None  # "sum" | "count" | "avg" | None
    confidence: float = 1.0


@dataclass(frozen=True)
class TimeConstraint:
    """Time window described in the question (absolute or relative)."""

    kind: str  # "absolute" | "relative" | "persian_year"
    raw_text: str
    start: Optional[str] = None
    end: Optional[str] = None


@dataclass(frozen=True)
class Reference:
    """A cross-question follow-up reference (e.g. pronoun resolution)."""

    text: str
    resolved_to: Optional[str] = None
    confidence: float = 1.0


@dataclass(frozen=True)
class AmbiguityReport:
    """Unresolved information flagged by QuestionAnalyzer."""

    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    clarifying_question: Optional[str] = None


@dataclass(frozen=True)
class QuestionSpec:
    """Typed representation of the user's question."""

    intent: str
    entities: tuple[Entity, ...] = ()
    filters: tuple[QuestionFilter, ...] = ()
    metrics: tuple[QuestionMetric, ...] = ()
    time_constraints: tuple[TimeConstraint, ...] = ()
    references: tuple[Reference, ...] = ()
    ambiguity: Optional[AmbiguityReport] = None
    confidence: float = 1.0
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Schema domain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableDescriptor:
    """Metadata for a single table."""

    name: str
    columns: tuple[str, ...] = ()
    primary_key: Optional[str] = None
    foreign_keys: tuple["Relationship", ...] = ()


@dataclass(frozen=True)
class Relationship:
    """Foreign-key or named relationship between two tables."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    name: Optional[str] = None


@dataclass(frozen=True)
class BusinessSemantics:
    """Domain-level formula or note tied to the schema."""

    description: str
    formula: Optional[str] = None
    applies_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaVersion:
    """Identifier for a SchemaSnapshot (id, captured_at, source)."""

    id: str
    captured_at: datetime
    source: str


@dataclass(frozen=True)
class SchemaSnapshot:
    """Immutable schema metadata snapshot."""

    version: SchemaVersion
    tables: tuple[TableDescriptor, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    business_semantics: tuple[BusinessSemantics, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TableRef:
    """Qualified table reference used in planning and SQL."""

    name: str
    alias: Optional[str] = None


@dataclass(frozen=True)
class ColumnRef:
    """Qualified column reference."""

    table: str
    column: str


@dataclass(frozen=True)
class JoinHint:
    """FK-derived join path suggested by the SchemaRetriever."""

    left: ColumnRef
    right: ColumnRef
    kind: str = "inner"  # "inner" | "left" | "right"


@dataclass(frozen=True)
class SchemaContext:
    """Subset of schema allowed for the planner/generator."""

    tables: tuple[TableRef, ...] = ()
    columns: tuple[ColumnRef, ...] = ()
    joins: tuple[JoinHint, ...] = ()
    relevant_business_semantics: tuple[BusinessSemantics, ...] = ()
    confidence: float = 1.0
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Planning domain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Join:
    """A concrete join in the QueryPlan."""

    left: TableRef
    right: TableRef
    on_left: str
    on_right: str
    kind: str = "inner"


@dataclass(frozen=True)
class Dimension:
    """Group-by column."""

    column: ColumnRef
    alias: Optional[str] = None


@dataclass(frozen=True)
class PlanMetric:
    """Aggregation with explicit function and output alias.

    Distinct from `QuestionMetric` (which is the user-intent form).
    """

    column: Optional[ColumnRef] = None
    function: str = "count"  # "sum" | "count" | "avg" | "min" | "max"
    alias: str = ""


@dataclass(frozen=True)
class PlanFilter:
    """Filter attached to the QueryPlan (typed for SQL generation).

    Distinct from `QuestionFilter` (which carries the intent form).
    """

    column: ColumnRef
    operator: str  # "eq" | "ne" | "in" | "between" | "like" | "gt" | "lt"
    value: Any = None


@dataclass(frozen=True)
class SortKey:
    """Sort key in the QueryPlan."""

    column: ColumnRef
    direction: str = "asc"  # "asc" | "desc"


@dataclass(frozen=True)
class OutputRequirements:
    """Output formatting hints for the planner."""

    limit: Optional[int] = None
    offset: Optional[int] = None
    distinct: bool = False


@dataclass(frozen=True)
class QueryPlan:
    """Typed plan produced by QueryPlanner."""

    tables: tuple[TableRef, ...] = ()
    joins: tuple[Join, ...] = ()
    dimensions: tuple[Dimension, ...] = ()
    metrics: tuple[PlanMetric, ...] = ()
    filters: tuple[PlanFilter, ...] = ()
    post_filters: tuple[PlanFilter, ...] = ()
    sorting: tuple[SortKey, ...] = ()
    output_requirements: OutputRequirements = field(default_factory=OutputRequirements)
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# SQL generation / validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SQLCandidate:
    """Raw SQL produced by SQLGenerator or SQLRepairer.

    SQL is *untrusted* at this stage; it MUST be validated by
    SecurityValidator before any DB call.
    """

    sql: str
    dialect: str
    candidate_id: str
    fingerprint: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    prompt_strategy: str = "zero-shot"


@dataclass(frozen=True)
class ParsedAST:
    """Wrapper for a parsed SQL AST (sqlglot expression).

    The actual expression type is opaque to most components. Components
    that walk the AST (SemanticValidator, ComplexityValidator) import
    sqlglot directly.
    """

    expression: Any  # sqlglot.exp.Expression
    dialect: str


@dataclass(frozen=True)
class SyntaxError:
    """A single syntax error reported by SQLValidator."""

    message: str
    line: Optional[int] = None
    column: Optional[int] = None


@dataclass(frozen=True)
class ValidationReport:
    """Result of SQLValidator."""

    candidate_id: str
    valid: bool
    parse_ok: bool
    syntax_errors: tuple[SyntaxError, ...] = ()
    ast: Optional[ParsedAST] = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticViolation:
    """Single semantic mismatch."""

    code: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass(frozen=True)
class SemanticReport:
    """Result of SemanticValidator."""

    candidate_id: str
    ok: bool
    violations: tuple[SemanticViolation, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComplexityBudget:
    """Allowed maximum complexity for one SQL candidate."""

    max_joins: int = 5
    max_tables: int = 5
    max_depth: int = 8
    max_functions: int = 20
    max_estimated_cost: str = "medium"  # "low" | "medium" | "high"


@dataclass(frozen=True)
class ComplexityReport:
    """Result of ComplexityValidator."""

    ok: bool
    score: float = 0.0
    violations: tuple[str, ...] = ()
    estimated_cost: str = "low"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryResult:
    """Result returned by QueryExecutor."""

    columns: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    row_count: int = 0
    truncated: bool = False
    duration_ms: float = 0.0
    executed_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Result / Presentation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnType:
    """Inferred column type for ResultAnalyzer."""

    name: str
    python_type: str  # "int" | "float" | "str" | "datetime" | "bool" | "other"
    nullable: bool = True


@dataclass(frozen=True)
class ShapeDescriptor:
    """Row/column shape of a result."""

    rows: int
    columns: int


@dataclass(frozen=True)
class AggregateRef:
    """Provenance for an aggregated value."""

    metric_alias: str
    source_column: str
    function: str


@dataclass(frozen=True)
class ResultProfile:
    """Typed profile of a QueryResult."""

    shape: ShapeDescriptor
    column_types: Mapping[str, ColumnType] = field(default_factory=dict)
    null_counts: Mapping[str, int] = field(default_factory=dict)
    row_count: int = 0
    truncated: bool = False
    aggregate_provenance: Mapping[str, AggregateRef] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormatSpec:
    """Per-column format specification."""

    number_format: Optional[str] = None
    date_format: Optional[str] = None
    currency: Optional[str] = None
    unit: Optional[str] = None


@dataclass(frozen=True)
class LocalePolicy:
    """Locale-driven presentation rules."""

    locale: str = "fa_IR"
    rtl: bool = True
    number_locale: str = "fa_IR"


@dataclass(frozen=True)
class PresentationPlan:
    """Deterministic plan for displaying rows."""

    visible_columns: tuple[str, ...] = ()
    column_order: tuple[str, ...] = ()
    display_labels: Mapping[str, str] = field(default_factory=dict)
    formats: Mapping[str, FormatSpec] = field(default_factory=dict)
    view_kind: str = "table"  # "table" | "kpi" | "ranking" | "timeseries"
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Answer domain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """A single asserted fact in an answer."""

    text: str
    value: Any = None
    source: Optional[str] = None  # e.g. "row:3 col:total_revenue"


@dataclass(frozen=True)
class Evidence:
    """Auxiliary context supporting a claim.

    Shared between QuestionSpec.references (resolved earlier) and
    AnswerDraft.citations. The two domains refer to the same shape but
    populate it from different sources.
    """

    source: str
    content: str
    confidence: float = 1.0


@dataclass(frozen=True)
class AnswerDraft:
    """Draft natural-language answer from AnswerGenerator."""

    text: str
    format: str = "markdown-persian"
    claims: tuple[Claim, ...] = ()
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedAnswer:
    """Validated, sanitised answer."""

    text: str
    accepted_claims: tuple[Claim, ...] = ()
    rejected_claims: tuple[Claim, ...] = ()
    sanitized: bool = False
    format_ok: bool = True
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    """LLM token usage metadata."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


@dataclass(frozen=True)
class LLMPolicy:
    """Provider selection policy for LLMRouter."""

    provider: Optional[str] = None  # "openai" | "gemini" | "groq" | "ollama"
    model: Optional[str] = None
    fallback_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMBudget:
    """Resource budget for an LLM call."""

    max_tokens: Optional[int] = None
    timeout_ms: Optional[int] = None
    max_latency_ms: Optional[int] = None


@dataclass(frozen=True)
class LLMResult:
    """Result of an LLMRouter call."""

    text: str
    provider: str
    model: str
    usage: Usage
    latency_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed error hierarchy (ADR-015)
# ---------------------------------------------------------------------------


class ErrorStageTag(str, Enum):
    """Stage tag for a TypedComponentError."""

    QUESTION = "question"
    SCHEMA = "schema"
    PLANNING = "planning"
    SQL_GENERATION = "sql_generation"
    SECURITY = "security"
    VALIDATION = "validation"
    SEMANTIC = "semantic"
    COMPLEXITY = "complexity"
    REPAIR = "repair"
    EXECUTION = "execution"
    RESULT = "result"
    PRESENTATION = "presentation"
    ANSWER = "answer"
    ANSWER_VALIDATION = "answer_validation"
    MEMORY = "memory"
    LLM = "llm"
    INTERNAL = "internal"


class TypedComponentError(Exception):
    """Base typed error carrying the ADR-015 fields.

    Fields:
        code      — Stable machine-readable code (see ADR-015 registry).
        message   — Internal-only message; never shown to user.
        user_message — Safe UI text.
        stage     — Pipeline stage tag (ErrorStageTag).
        retryable — Whether the orchestrator may retry.
        cause     — Underlying exception (not serialized across boundaries).
    """

    code: str = "COMP.INTERNAL"
    default_user_message: str = "An internal error occurred."
    default_stage: ErrorStageTag = ErrorStageTag.INTERNAL
    default_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        user_message: Optional[str] = None,
        stage: Optional[ErrorStageTag] = None,
        retryable: Optional[bool] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        self.code = code or self.code
        self.message = message
        self.user_message = user_message or self.default_user_message
        self.stage = stage or self.default_stage
        self.retryable = self.default_retryable if retryable is None else retryable
        self.cause = cause
        super().__init__(message)


class QuestionAnalysisError(TypedComponentError):
    code = "QA.INTERNAL"
    default_user_message = "Could not understand the question."
    default_stage = ErrorStageTag.QUESTION


class SchemaError(TypedComponentError):
    code = "SCH.INTERNAL"
    default_user_message = "Schema information is not available."
    default_stage = ErrorStageTag.SCHEMA


class PlanningError(TypedComponentError):
    code = "PLN.INTERNAL"
    default_user_message = "Could not build a query plan."
    default_stage = ErrorStageTag.PLANNING


class SQLGenerationError(TypedComponentError):
    code = "GEN.INTERNAL"
    default_user_message = "Could not generate the SQL query."
    default_stage = ErrorStageTag.SQL_GENERATION


class RepairError(TypedComponentError):
    code = "RPR.INTERNAL"
    default_user_message = "SQL repair failed."
    default_stage = ErrorStageTag.REPAIR


class ResultAnalysisError(TypedComponentError):
    code = "RES.INTERNAL"
    default_user_message = "Could not analyze the result."
    default_stage = ErrorStageTag.RESULT


class PresentationError(TypedComponentError):
    code = "PRE.INTERNAL"
    default_user_message = "Could not prepare the presentation."
    default_stage = ErrorStageTag.PRESENTATION


class AnswerGenerationError(TypedComponentError):
    code = "ANS.INTERNAL"
    default_user_message = "Could not generate the answer."
    default_stage = ErrorStageTag.ANSWER


class AnswerValidationError(TypedComponentError):
    code = "ANV.INTERNAL"
    default_user_message = "Answer validation failed."
    default_stage = ErrorStageTag.ANSWER_VALIDATION


class MemoryError(TypedComponentError):
    code = "MEM.INTERNAL"
    default_user_message = "Memory subsystem is unavailable."
    default_stage = ErrorStageTag.MEMORY


class LLMRouterError(TypedComponentError):
    code = "ROU.INTERNAL"
    default_user_message = "LLM subsystem is unavailable."
    default_stage = ErrorStageTag.LLM
    default_retryable = True


# ---------------------------------------------------------------------------
# Component interfaces (Protocols)
# ---------------------------------------------------------------------------


@runtime_checkable
class QuestionAnalyzer(Protocol):
    """Convert a user question into a typed QuestionSpec."""

    def analyze(
        self,
        question: str,
        history: Sequence[Mapping[str, str]],
        *,
        active_filters: Optional[Mapping[str, Any]] = None,
        resolved_references: Optional[Mapping[str, Any]] = None,
    ) -> QuestionSpec: ...


@runtime_checkable
class SchemaCatalog(Protocol):
    """Live, versioned schema metadata owner."""

    def current_version(self) -> str: ...

    def versions(self) -> tuple[SchemaVersion, ...]: ...

    def get_snapshot(self, version: Optional[str] = None) -> SchemaSnapshot: ...


@runtime_checkable
class SchemaRetriever(Protocol):
    """Pick the schema subset allowed for a QuestionSpec."""

    def retrieve(
        self,
        spec: QuestionSpec,
        snapshot: SchemaSnapshot,
    ) -> SchemaContext: ...


@runtime_checkable
class QueryPlanner(Protocol):
    """Build a typed QueryPlan from QuestionSpec + SchemaContext."""

    def plan(
        self,
        spec: QuestionSpec,
        context: SchemaContext,
    ) -> QueryPlan: ...


@runtime_checkable
class SQLGenerator(Protocol):
    """Emit a raw SQLCandidate from a QueryPlan + SchemaContext."""

    def generate(
        self,
        plan: QueryPlan,
        context: SchemaContext,
        *,
        dialect: str = "tsql",
    ) -> SQLCandidate: ...


@runtime_checkable
class SQLValidator(Protocol):
    """Syntactic validation; produces ValidationReport."""

    def validate(self, candidate: SQLCandidate, *, dialect: str = "tsql") -> ValidationReport: ...


@runtime_checkable
class SecurityValidator(Protocol):
    """Fail-closed security decision; produces SecurityDecision."""

    def validate(self, sql: str, *, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY) -> SecurityDecision: ...


@runtime_checkable
class SemanticValidator(Protocol):
    """Validate semantic consistency of a SQL candidate."""

    def validate(
        self,
        candidate: SQLCandidate,
        plan: QueryPlan,
        snapshot: SchemaSnapshot,
        ast: Optional[ParsedAST] = None,
    ) -> SemanticReport: ...


@runtime_checkable
class ComplexityValidator(Protocol):
    """Reject SQL candidates that exceed a configured budget."""

    def validate(self, ast: ParsedAST, budget: ComplexityBudget) -> ComplexityReport: ...


@runtime_checkable
class SQLRepairer(Protocol):
    """Bounded retry producing a new SQLCandidate from validator diagnostics."""

    def repair(
        self,
        candidate: SQLCandidate,
        *,
        validation: Optional[ValidationReport] = None,
        security: Optional[SecurityDecision] = None,
        semantic: Optional[SemanticReport] = None,
        complexity: Optional[ComplexityReport] = None,
        plan: QueryPlan,
        attempt: int,
    ) -> SQLCandidate: ...


@runtime_checkable
class QueryExecutor(Protocol):
    """Only component allowed to talk to SQL Server."""

    def execute(
        self,
        sql: str,
        *,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
        policy: ExecutionPolicy = ...,
    ) -> QueryResult: ...


@runtime_checkable
class ResultAnalyzer(Protocol):
    """Inspect a QueryResult and produce a ResultProfile."""

    def profile(self, result: QueryResult, plan: QueryPlan) -> ResultProfile: ...


@runtime_checkable
class PresentationPlanner(Protocol):
    """Plan how to display rows."""

    def plan(
        self,
        spec: QuestionSpec,
        profile: ResultProfile,
        *,
        labels: Mapping[str, str],
        locale_policy: LocalePolicy,
    ) -> PresentationPlan: ...


@runtime_checkable
class AnswerGenerator(Protocol):
    """Generate natural-language answer grounded in result data."""

    def generate(
        self,
        spec: QuestionSpec,
        profile: ResultProfile,
        presentation: PresentationPlan,
        result: QueryResult,
        *,
        evidence: Sequence[Evidence] = (),
    ) -> AnswerDraft: ...


@runtime_checkable
class AnswerValidator(Protocol):
    """Validate and sanitise an AnswerDraft."""

    def validate(
        self,
        draft: AnswerDraft,
        profile: ResultProfile,
        presentation: PresentationPlan,
    ) -> ValidatedAnswer: ...


@runtime_checkable
class MemoryManagerProtocol(Protocol):
    """Memory boundary (typed turn model deferred to Sprint 11)."""

    def create_session(self) -> str: ...

    def session_exists(self, session_id: str) -> bool: ...

    def add_message(self, session_id: str, role: str, content: str) -> None: ...

    def get_history(self, session_id: str, limit: int = 5) -> list[dict[str, str]]: ...

    def clear_session(self, session_id: str) -> None: ...


@runtime_checkable
class LLMRouter(Protocol):
    """Single entry point for all LLM calls."""

    def generate(
        self,
        prompt: str,
        *,
        task: str,
        policy: Optional[LLMPolicy] = None,
        budget: Optional[LLMBudget] = None,
    ) -> LLMResult: ...