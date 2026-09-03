# Agent State Design — Target Architecture

**Date**: 2026-08-29
**Sprint**: 2 — Prompt 1A (design) / Prompt 2B (additions for typed value objects)
**Status**: Design complete and implemented. Sprint 2 Prompt 2B added six
new state-applier methods (`with_question_analysis`,
`with_schema_context`, `with_query_plan`, `with_result_profile`,
`with_presentation_plan`, `with_validated_answer`) and re-typed
`SQLState.security_decision` as `Optional[SecurityDecision]`.

---

## Purpose

Define the authoritative `AgentState` model that carries data through the Text-to-SQL pipeline stages. This model separates **business/application state** from **infrastructure/runtime context**, provides predictable transitions, enforces invariants, and enables safe observability.

---

## Scope

- **In Scope**: `AgentState` and all sub-models in `src/agents/state.py`
- **In Scope**: State transitions, invariants, serialization, boundaries
- **Out of Scope**: Orchestrator, pipeline components, API contracts, persistence

---

## High-Level Structure

```
AgentState
├── request: RequestContext           # Immutable request identity
├── conversation: ConversationContext # Conversation snapshot (no storage deps)
├── pipeline_stage: PipelineStage     # Explicit stage machine
├── question_analysis: QuestionSpec?  # Future: ADR-001
├── schema_context: SchemaContext?    # Future: ADR-002
├── query_plan: QueryPlan?            # Future: ADR-003
├── sql: SQLState                     # SQL generation + validation
├── execution: ExecutionState         # Database execution results
├── result_profile: ResultProfile?    # Future: ADR-007
├── presentation_plan: PresentationPlan? # Future: ADR-008
├── response: ResponseState           # Final answer + metadata
├── error: ErrorState                 # Structured error info
└── metadata: RuntimeMetadata         # Observability (separate from business)
```

---

## Detailed Field Catalog

### RequestContext (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| request_id | str | Yes | uuid4().hex | Request | Created once | No | Yes | No |
| trace_id | str | Yes | uuid4().hex | Infrastructure | Created once | No | Yes | No |
| session_id | str? | No | None | Session | Set at start | No* | Yes | No |
| question | str | Yes | "" | Request | Created once | No | Yes** | **Yes** |
| timestamp | datetime | Yes | utcnow() | Request | Created once | No | Yes | No |

*Session ID can be updated once via `with_session()` before pipeline starts.
**Redacted in safe serialization (`include_sensitive=False`).

### ConversationContext (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| history | tuple[dict, ...] | No | () | Memory | Set at start | No | Yes (count only) | No |
| resolved_references | dict | No | {} | Analyzer | Future | No | Yes | No |
| active_filters | dict | No | {} | Analyzer | Future | No | Yes | No |

### PipelineStage (Enum)
```
INITIALIZED → QUESTION_ANALYZED → SCHEMA_RESOLVED → PLAN_CREATED
    → SQL_GENERATED → VALIDATED → EXECUTED → RESULT_ANALYZED → ANSWERED
                    ↘ FAILED (from any stage)
```

### SQLState (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| generated_sql | str? | No | None | Generator | SQL_GENERATED | No | Yes** | **Yes** |
| sql_fingerprint | str? | No | None | Generator | SQL_GENERATED | No | Yes | No |
| normalized_sql | str? | No | None | Validator | VALIDATED | No | Yes** | **Yes** |
| validation_status | ValidationStatus | Yes | PENDING | Validator | VALIDATED | No | Yes | No |
| validation_message | str? | No | None | Validator | VALIDATED | No | Yes | No |
| validation_reason_code | str? | No | None | Validator | VALIDATED | No | Yes | No |
| security_decision | SecurityDecision? | No | None | Security | VALIDATED | No | Yes (summary) | No |
| semantic_status | ValidationStatus? | No | None | Semantic | RESULT_ANALYZED | No | Yes | No |
| correction_attempt | int | Yes | 0 | Generator | SQL_GENERATED+ | No | Yes | No |
| max_correction_attempts | int | Yes | 2 | Config | Created once | No | Yes | No |

### ExecutionState (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| status | ExecutionStatus | Yes | PENDING | Executor | EXECUTED | No | Yes | No |
| rows | tuple[dict, ...] | No | () | Executor | EXECUTED | No | Yes** | **Yes** |
| row_count | int | Yes | 0 | Executor | EXECUTED | No | Yes | No |
| columns | tuple[str, ...] | No | () | Executor | EXECUTED | No | Yes | No |
| duration_ms | float? | No | None | Executor | EXECUTED | No | Yes | No |
| error_code | str? | No | None | Executor | EXECUTED/FAILED | No | Yes | No |
| error_message | str? | No | None | Executor | EXECUTED/FAILED | No | Yes | No |
| database_error_code | str? | No | None | Executor | EXECUTED/FAILED | No | Yes | No |
| truncated | bool | Yes | False | Executor | EXECUTED | No | Yes | No |

### ResponseState (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| answer | str? | No | None | AnswerGen | ANSWERED | No | Yes** | **Yes** |
| success | bool | Yes | False | Pipeline | ANSWERED/FAILED | No | Yes | No |
| error | str? | No | None | Pipeline | FAILED | No | Yes | No |
| metadata | dict | No | {} | Presenter | ANSWERED | No | Yes | No |
| presentation_plan | PresentationPlan? | No | None | Presenter | ANSWERED | No | Yes | No |

### ErrorState (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| category | ErrorCategory? | No | None | Pipeline | FAILED | No | Yes | No |
| code | str? | No | None | Pipeline | FAILED | No | Yes | No |
| message | str? | No | None | Pipeline | FAILED | No | Yes | No |
| user_facing_message | str? | No | None | Pipeline | FAILED | No | Yes | No |
| stage | PipelineStage? | No | None | Pipeline | FAILED | No | Yes | No |
| retryable | bool | Yes | False | Pipeline | FAILED | No | Yes | No |
| details | dict | No | {} | Pipeline | FAILED | No | Yes | No |
| trace_reference | str? | No | None | Observability | FAILED | No | Yes | No |

### RuntimeMetadata (Frozen)
| Field | Type | Required | Default | Owner | Lifecycle | Mutable | Serializable | Sensitive |
|-------|------|----------|---------|-------|-----------|---------|--------------|-----------|
| stage_timings | dict[str, float] | No | {} | Observability | Throughout | No | Yes | No |
| stage_start_times | dict[str, float] | No | {} | Observability | Throughout | No | No (internal) | No |
| llm_calls | int | Yes | 0 | Observability | Throughout | No | Yes | No |
| llm_total_duration_ms | float | Yes | 0.0 | Observability | Throughout | No | Yes | No |
| model_provider | str? | No | None | Observability | LLM calls | No | Yes | No |
| model_name | str? | No | None | Observability | LLM calls | No | Yes | No |
| token_usage | dict[str, int] | No | {} | Observability | LLM calls | No | Yes | No |
| attempt_counters | dict[str, int] | No | {} | Observability | Throughout | No | Yes | No |
| validator_calls | int | Yes | 0 | Observability | VALIDATED | No | Yes | No |

---

## State Ownership Model

| State Component | Owner | Rationale |
|----------------|-------|-----------|
| RequestContext | Request/Application | Created at API entry, immutable |
| ConversationContext | Memory/Application | Snapshot from MemoryManager at start |
| PipelineStage | Orchestrator | Single writer, controls flow |
| SQLState | SQLGenerator + Validators | Generator writes, validators update status |
| ExecutionState | QueryExecutor | Sole writer at execution boundary |
| ResponseState | AnswerGenerator + Presenter | Generator writes answer, presenter adds plan |
| ErrorState | Pipeline (error handlers) | Single writer at failure points |
| RuntimeMetadata | Observability | Multiple writers (timing, counters), read-only for business logic |

**Key Principle**: Each state component has a **single logical owner** (component/stage) that writes it. Other components read but do not mutate.

---

## Boundaries

### AgentState ↔ TraceContext
```
TraceContext (infrastructure)
    = request correlation for logging/observability
    = contextvar-based, request-scoped
    = carries: trace_id, session_id (for logging convenience)

AgentState.request (business)
    = request identity for processing
    = carries: request_id, trace_id (reference), session_id, question, timestamp

Boundary Rule:
    - AgentState created with trace_id FROM TraceContext at request start
    - AgentState does NOT depend on TraceContext after creation
    - TraceContext does NOT depend on AgentState
    - Logging uses TraceContext; business logic uses AgentState.request
```

### AgentState ↔ MemoryManager
```
MemoryManager (infrastructure)
    = SQLite persistence, session lifecycle, message storage
    = API: create_session(), get_history(), add_message(), session_exists(), clear_session()

AgentState.conversation (business snapshot)
    = Immutable tuple of history messages at request start
    = No storage dependency, no MemoryManager reference
    = Future: resolved_references, active_filters from QuestionAnalyzer

Boundary Rule:
    - TextToSQLAgent calls MemoryManager BEFORE/AFTER AgentState updates
    - AgentState never holds MemoryManager instance or connection
    - MemoryManager never holds AgentState reference
```

### AgentState ↔ DatabaseConnection
```
DatabaseConnection (infrastructure)
    = pymssql connection/cursor management, execution policy, error translation
    = API: execute_query(sql) → list[dict], execute_non_query() → fail-closed

AgentState.execution (business result)
    = Immutable snapshot of execution outcome
    = No connection, cursor, transaction, driver objects

Boundary Rule:
    - QueryExecutor (inside DatabaseConnection) writes ExecutionState
    - AgentState.execution populated from ExecutionState after execution
    - No database resources in AgentState
```

### AgentState ↔ SecurityValidator
```
SQLSecurityValidator (infrastructure/policy)
    = AST parsing, policy evaluation, SecurityDecision
    = Stateless, no external dependencies

AgentState.sql (business)
    = Holds SecurityDecision summary (valid, reason_code, message)
    = Does NOT hold validator instance or AST

Boundary Rule:
    - Validator runs, produces SecurityDecision
    - AgentState.sql.validation_status updated from decision
    - SecurityDecision.to_dict() stored if needed for debugging
```

### AgentState ↔ Observability
```
RuntimeMetadata (in AgentState)
    = Structured telemetry: timings, counters, model info
    = Part of AgentState for single-state serialization

Logger/TraceContext (external)
    = Event logging, correlation, redaction
    = Consumes AgentState.to_dict(include_sensitive=False)

Boundary Rule:
    - RuntimeMetadata is business-state-adjacent telemetry
    - Logger is separate infrastructure
    - No circular dependency
```

---

## Valid State Transitions (State Machine)

```python
VALID_TRANSITIONS = {
    PipelineStage.INITIALIZED: {
        PipelineStage.QUESTION_ANALYZED,    # Future: QuestionAnalyzer
        PipelineStage.SQL_GENERATED,        # Current: direct generation
        PipelineStage.FAILED,
    },
    PipelineStage.QUESTION_ANALYZED: {
        PipelineStage.SCHEMA_RESOLVED,      # Future: SchemaRetriever
        PipelineStage.FAILED,
    },
    PipelineStage.SCHEMA_RESOLVED: {
        PipelineStage.PLAN_CREATED,         # Future: QueryPlanner
        PipelineStage.FAILED,
    },
    PipelineStage.PLAN_CREATED: {
        PipelineStage.SQL_GENERATED,        # Future: SQLGenerator
        PipelineStage.FAILED,
    },
    PipelineStage.SQL_GENERATED: {
        PipelineStage.VALIDATED,            # Current: validation
        PipelineStage.FAILED,
    },
    PipelineStage.VALIDATED: {
        PipelineStage.EXECUTED,             # Current: execution
        PipelineStage.FAILED,
    },
    PipelineStage.EXECUTED: {
        PipelineStage.RESULT_ANALYZED,      # Future: ResultAnalyzer
        PipelineStage.ANSWERED,             # Current: direct answer
        PipelineStage.FAILED,
    },
    PipelineStage.RESULT_ANALYZED: {
        PipelineStage.ANSWERED,             # Future: PresentationPlanner + AnswerGenerator
        PipelineStage.FAILED,
    },
    PipelineStage.ANSWERED: set(),          # Terminal success
    PipelineStage.FAILED: set(),            # Terminal failure
}
```

**Transition Rules**:
1. Transitions ONLY via `AgentState.transition_to(new_stage)` which validates against `VALID_TRANSITIONS`
2. Convenience methods (e.g., `with_generated_sql()`) combine data update + transition
3. Error paths transition to `FAILED` and populate `ErrorState`
4. No backward transitions, no skipping required stages (except current architecture shortcuts)

---

## State Invariants

| ID | Rule | Why | Enforcement |
|----|------|-----|-------------|
| INV-001 | If `execution.status == SUCCESS` and `row_count > 0` → `rows` not empty | Execution success implies data present | `validate_invariants()` |
| INV-002 | If `sql.is_security_rejected` → `execution.status == PENDING` | Security rejection prevents execution | `validate_invariants()` |
| INV-003 | If `response.success == True` → `response.answer` not None | Successful response must have answer | `validate_invariants()` |
| INV-004 | If `response.success == False` → `response.error` not None | Failed response must have error | `validate_invariants()` |
| INV-005 | If `pipeline_stage == FAILED` → `error.category` not None | Failed pipeline must have error category | `validate_invariants()` |
| INV-006 | If `sql.validation_status == PASSED` → `sql.generated_sql` not None | Validated SQL must exist | `validate_invariants()` |
| INV-007 | If `sql.correction_attempt > 0` → `sql.generated_sql` not None | Correction requires base SQL | `validate_invariants()` |
| INV-008 | `metadata.llm_calls` >= 0 | Counters never negative | Dataclass field default |
| INV-009 | `sql.correction_attempt` <= `sql.max_correction_attempts` | Attempts bounded by config | `with_correction_attempt()` checks |
| INV-010 | If `execution.truncated == True` → `execution.status == ROW_LIMIT_EXCEEDED` | Truncation implies limit exceeded | `ExecutionState.with_results()` |

**Enforcement Location**: `AgentState.validate_invariants()` called at stage transitions and before serialization. Violations logged as warnings, not exceptions (observability).

---

## Mutability Model

### Sub-Models (All Frozen)
```
RequestContext      - frozen=True
ConversationContext - frozen=True
SQLState            - frozen=True
ExecutionState      - frozen=True
ResponseState       - frozen=True
ErrorState          - frozen=True
RuntimeMetadata     - frozen=True
```

**Update Pattern**: `dataclasses.replace(instance, field=new_value)` returns new instance.

### AgentState (Mutable Container)
```python
@dataclass  # NOT frozen
class AgentState:
    request: RequestContext
    conversation: ConversationContext
    pipeline_stage: PipelineStage
    sql: SQLState
    execution: ExecutionState
    response: ResponseState
    error: ErrorState
    metadata: RuntimeMetadata
    # ... future placeholders
```

**Rationale**:
- Sub-models immutable → predictable, thread-safe, safe for logging
- AgentState mutable → method chaining ergonomics (`state.with_x().with_y()`)
- All mutations return `replace(self, ...)` → functional updates, no in-place mutation
- Single writer per sub-model enforced by convention + transition methods

### Who Can Update What

| Method | Updates | Called By |
|--------|---------|-----------|
| `with_generated_sql()` | sql, pipeline_stage | SQLGenerator (or SQLExtractor via adapter) |
| `with_validation_passed()` | sql, pipeline_stage | Validator |
| `with_validation_failed()` | sql, error | Validator |
| `with_security_rejected()` | sql, error, pipeline_stage | SecurityValidator |
| `with_correction_attempt()` | sql | SQLGenerator (retry) |
| `with_execution_results()` | execution, pipeline_stage | QueryExecutor |
| `with_execution_error()` | execution, error, pipeline_stage | QueryExecutor |
| `with_answer()` | response, pipeline_stage | AnswerGenerator |
| `with_error_response()` | response, error, pipeline_stage | Pipeline error handler |
| `with_question_analysis()` | question_analysis, pipeline_stage → QUESTION_ANALYZED | Orchestrator (after QuestionAnalyzer) |
| `with_schema_context()` | schema_context, pipeline_stage → SCHEMA_RESOLVED | Orchestrator (after SchemaRetriever) |
| `with_query_plan()` | query_plan, pipeline_stage → PLAN_CREATED | Orchestrator (after QueryPlanner) |
| `with_result_profile()` | result_profile, pipeline_stage → RESULT_ANALYZED | Orchestrator (after ResultAnalyzer) |
| `with_presentation_plan()` | presentation_plan | Orchestrator (after PresentationPlanner; does not transition stage) |
| `with_validated_answer()` | validated_answer, response, pipeline_stage → ANSWERED | Orchestrator (after AnswerValidator) |
| `with_stage_timing()` | metadata | Orchestrator |
| `with_llm_call()` | metadata | LLMClient wrapper |
| `increment_attempt()` | metadata | Retry logic |
| `transition_to()` | pipeline_stage | Any stage transition |

The six appliers added in Prompt 2B
(`with_question_analysis` / `with_schema_context` / `with_query_plan` /
`with_result_profile` / `with_presentation_plan` /
`with_validated_answer`) are the typed-value-object surface that the new
`Orchestrator` uses to write into `AgentState`. They are validated by
`tests/test_agents/test_state_appliers.py`.

---

## Serialization Strategy

### `to_dict(include_sensitive: bool = False)`
```python
{
    "request": {
        "request_id": "...",
        "trace_id": "...",
        "session_id": "...",
        "question": "..." if include_sensitive else "[REDACTED]",
        "timestamp": "2026-08-29T..."
    },
    "conversation": {
        "history_length": 3,
        "has_resolved_references": false,
        "has_active_filters": false
    },
    "pipeline_stage": "sql_generated",
    "sql": {
        "has_sql": true,
        "sql_fingerprint": "sha256...",
        "validation_status": "passed",
        "correction_attempt": 0,
    },
    "execution": {
        "status": "success",
        "row_count": 42,
        "duration_ms": 123.4,
        "error_code": null,
    },
    "response": {
        "success": true,
        "has_answer": true,
        "error": null,
    },
    "error": null,  # or error.to_dict()
    "metadata": {
        "stage_timings": {"sql_generation": 456.7, ...},
        "llm_calls": 2,
        "llm_total_duration_ms": 890.1,
        "model_provider": "openai",
        "model_name": "gpt-4o-mini",
    }
}
```

### `state_to_api_response(state)` — Backward Compatible
```python
# Success
{
    "success": True,
    "question": "...",
    "answer": "...",
    "sql": "...",
    "row_count": 42,
    "rows": [...],
    "session_id": "..."
}

# Failure
{
    "success": False,
    "question": "...",
    "error": "...",
    "sql": "...",
    "session_id": "...",
    "database_error_code": "..."  # optional
}
```

### Persistence
- **Not in scope for Sprint 2**
- `to_dict(include_sensitive=True)` provides full state for future persistence
- No automatic serialization hooks

---

## Sensitive Data Policy

| Field | Store | Log | Serialize (safe) | Serialize (full) | Expose (API) |
|-------|-------|-----|------------------|------------------|--------------|
| request.question | Yes | Redacted | Redacted | Plain | Yes |
| request.trace_id | Yes | Plain | Plain | Plain | No (header only) |
| request.session_id | Yes | Plain | Plain | Plain | Yes |
| sql.generated_sql | Yes | Redacted | Redacted | Plain | Yes |
| sql.normalized_sql | Yes | Redacted | Redacted | Plain | No |
| execution.rows | Yes | Redacted | Redacted | Plain | Yes |
| response.answer | Yes | Redacted | Redacted | Plain | Yes |
| error.message | Yes | Plain | Plain | Plain | Yes (sanitized) |
| metadata.* | Yes | Plain | Plain | Plain | No |

**Redaction Implementation**: `to_dict(include_sensitive=False)` + logger `sanitize()` both apply.

---

## Type System Decision

**Choice**: Python `dataclass` (stdlib)

**Rationale**:
| Factor | Decision |
|--------|----------|
| Existing conventions | `SecurityDecision`, `ExecutionPolicy`, `TraceContext` all use dataclass |
| Validation | Manual `validate_invariants()` sufficient; no complex schema validation needed |
| Serialization | Custom `to_dict()` with redaction control |
| Nested models | dataclass handles nesting naturally |
| Immutability | `frozen=True` on sub-models |
| Runtime safety | Type hints + mypy; no runtime validation overhead |
| Dependency boundary | Zero external deps; Pydantic only at API boundary |

**Not Chosen**:
- **Pydantic**: Overkill for internal state; API boundary already uses Pydantic
- **TypedDict**: No immutability, no methods, no validation
- **attrs**: External dependency; dataclass is stdlib since Python 3.7

---

## Request Isolation Guarantee

**Design**:
1. `create_initial_state()` creates fresh `AgentState` per request
2. All sub-models are frozen → no shared mutable references
3. `AgentState` methods return `replace(self, ...)` → new instances
4. `TextToSQLAgent` singleton holds only infrastructure (stateless services)
5. `TraceContext` uses `contextvars` → request-local

**Verification**:
- No class-level mutable state in `AgentState` or sub-models
- No global variables holding request state
- `agent_instance` global only holds infrastructure references
- Each `ask()` call: new `AgentState` → isolated

---

## Example State Shape (Conceptual)

```json
{
  "request": {
    "request_id": "a1b2c3d4",
    "trace_id": "trace-abc123",
    "session_id": "sess-xyz789",
    "question": "Show me total sales for 1400",
    "timestamp": "2026-08-29T10:30:00Z"
  },
  "conversation": {
    "history_length": 2,
    "has_resolved_references": false,
    "has_active_filters": false
  },
  "pipeline_stage": "executed",
  "sql": {
    "has_sql": true,
    "sql_fingerprint": "sha256:abc123...",
    "validation_status": "passed",
    "validation_message": null,
    "validation_reason_code": null,
    "correction_attempt": 0
  },
  "execution": {
    "status": "success",
    "row_count": 1,
    "duration_ms": 45.2,
    "error_code": null,
    "error_message": null,
    "database_error_code": null,
    "truncated": false
  },
  "response": {
    "success": true,
    "has_answer": true,
    "error": null,
    "metadata_keys": []
  },
  "error": null,
  "metadata": {
    "stage_timings": {
      "sql_generation": 1200.5,
      "validation": 12.3,
      "execution": 45.2,
      "answer_generation": 800.1
    },
    "llm_calls": 2,
    "llm_total_duration_ms": 2000.6,
    "model_provider": "openai",
    "model_name": "gpt-4o-mini",
    "attempt_counters": {}
  }
}
```

---

## Migration Considerations (for Prompt 1B)

1. **Backward Compatibility**: `state_to_api_response()` must preserve exact current API dict format
2. **CLI Compatibility**: `run.py` expects same response dict keys
3. **Streamlit Compatibility**: `src/web/app.py` expects same response dict keys
4. **Evaluation Adapter**: `EvaluationAgentAdapter.normalize_response()` expects same dict format
5. **No Breaking Changes**: All existing tests must pass without modification
6. **Incremental Wiring**: Wire `AgentState` into `TextToSQLAgent.ask()` internally; keep return format

---

## Open Questions

1. **Token Accounting**: Should `RuntimeMetadata.token_usage` be added now or wait for LLMRouter (ADR-011)?
   - *Recommendation*: Add field now as `dict[str, int] = {}`; populate when router exists

2. **SecurityDecision in SQLState**: Store full `SecurityDecision` or just summary?
   - *Recommendation*: Store `SecurityDecision` as `Any` for now; type when security types stabilized

3. **PipelineStage Granularity**: Current shortcuts (INITIALIZED→SQL_GENERATED, EXECUTED→ANSWERED) are temporary. Remove when full pipeline implemented?
   - *Recommendation*: Keep for now; transition validation allows them; remove in future sprint

4. **ErrorState.trace_reference**: Should this be `trace_id` or a separate correlation ID?
   - *Recommendation*: Use `trace_id` from `RequestContext`; add field when observability needs it

---

## Recommendation for Prompt 1B

**Implement**:
1. Wire `AgentState` fully into `TextToSQLAgent.ask()` (already partially done)
2. Replace all ad-hoc dict construction with `state_to_api_response()`
3. Add unit tests for:
   - `AgentState` creation and transitions
   - `validate_invariants()` all cases
   - `to_dict()` sensitive/non-sensitive modes
   - Request isolation (concurrent requests)
   - Serialization round-trip
4. Ensure all existing tests pass (101 tests from Sprint 1)
5. Add sensitive-data redaction tests

**Do Not**:
- Change API contracts (`AskRequest`, `AskResponse`)
- Change CLI or Streamlit behavior
- Add new pipeline components
- Implement persistence