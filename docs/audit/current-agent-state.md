# Current Agent State Flow Audit

**Date**: 2026-08-29
**Sprint**: 2 — Prompt 1A
**Status**: Complete audit for Sprint 2 Prompt 1A

---

## Current Data Flow in TextToSQLAgent

The current `TextToSQLAgent.ask()` method passes the following data through its execution:

### Request Identity
| Field | Source | Notes |
|-------|--------|-------|
| `question` | `AskRequest.question` | User input |
| `session_id` | `AskRequest.session_id` or `MemoryManager.create_session()` | Created if absent; unknown IDs silently replaced |
| `trace_id` | `TraceContext.trace_id` (contextvar) | Set at API entry, propagated via contextvars |

### Conversation Context
| Field | Source | Notes |
|-------|--------|-------|
| `history` | `MemoryManager.get_history(session_id, limit=5)` | Last 5 messages, role/content dicts |

### Pipeline Execution (Sequential, No Explicit Stage Model)
1. **SQL Generation** → `SQLExtractor.extract(question, history)` → `sql: str`
2. **SQL Execution** → `DatabaseConnection.execute_query(sql)` → `rows: list[dict]`
3. **Row Filtering/Translation** → `_filter_and_translate_rows(rows)` → `filtered_rows: list[dict]`
4. **Answer Generation** → `LLMClient.generate(prompts.natural_response_prompt(...))` → `answer: str` (skipped if no rows)

### Response Dictionary (Returned to API/CLI)
```python
{
    "question": question,           # str
    "sql": sql,                     # str
    "answer": answer,               # str
    "row_count": len(rows),         # int
    "rows": rows,                   # list[dict] (translated)
    "success": True,                # bool
    "session_id": session_id        # str
}
```

### Error Response Dictionary
```python
{
    "success": False,
    "error": "...",                 # str (sanitized for API)
    "question": question,           # str
    "sql": sql or None,             # str | None
    "session_id": session_id        # str
}
```

---

## Existing AgentState Implementation (src/agents/state.py)

The `src/agents/state.py` already defines a comprehensive AgentState model:

### Core Models
- `RequestContext` (request_id, trace_id, session_id, question, timestamp)
- `ConversationContext` (history, resolved_references, active_filters)
- `PipelineStage` enum (INITIALIZED → ANSWERED/FAILED)
- `SQLState` (generated_sql, fingerprint, validation_status, correction_attempt)
- `ExecutionState` (status, rows, row_count, duration_ms, error codes)
- `ResponseState` (answer, success, error, metadata)
- `ErrorState` (category, code, message, stage, retryable)
- `RuntimeMetadata` (stage_timings, llm_calls, attempt_counters)
- `AgentState` with transition methods and `to_dict(include_sensitive=False)`
- `create_initial_state()` factory
- `state_to_api_response()` for backward compatibility

### Mutability Strategy (Current Design)
`AgentState` uses **frozen dataclasses** for all sub-models (`RequestContext`, `ConversationContext`, `SQLState`, `ExecutionState`, `ResponseState`, `ErrorState`, `RuntimeMetadata`) with `replace()` for transitions.

`AgentState` itself is **mutable** (not frozen) to allow method chaining, but all sub-state updates return new instances via `replace()`.

### Serialization Strategy (Current Design)
- `to_dict(include_sensitive=False)` → safe for logging/observability (redacts question, SQL, rows, answer)
- `to_dict(include_sensitive=True)` → full state for debugging (explicit opt-in)
- No automatic persistence in this sprint (out of scope)
- `state_to_api_response()` → backward-compatible API dict

### Valid Stage Transitions
```python
VALID_TRANSITIONS = {
    PipelineStage.INITIALIZED: {QUESTION_ANALYZED, SQL_GENERATED, FAILED},
    PipelineStage.QUESTION_ANALYZED: {SCHEMA_RESOLVED, FAILED},
    PipelineStage.SCHEMA_RESOLVED: {PLAN_CREATED, FAILED},
    PipelineStage.PLAN_CREATED: {SQL_GENERATED, FAILED},
    PipelineStage.SQL_GENERATED: {VALIDATED, FAILED},
    PipelineStage.VALIDATED: {EXECUTED, FAILED},
    PipelineStage.EXECUTED: {RESULT_ANALYZED, ANSWERED, FAILED},
    PipelineStage.RESULT_ANALYZED: {ANSWERED, FAILED},
    PipelineStage.ANSWERED: set(),
    PipelineStage.FAILED: set(),
}
```

---

## Current State Gaps vs Target Architecture

| Missing | Current Handling |
|---------|------------------|
| Explicit pipeline stages | Implicit sequential execution |
| Question analysis structure | None (prompt embeds question) |
| Schema context | None (static FULL_SCHEMA in prompt) |
| Query plan | None (LLM generates directly) |
| Validation result structure | Exception-based (`SQLValidationError`, `SQLSecurityError`) |
| Execution metadata | Only `row_count`, no timing/error codes in response |
| Structured error state | Exception category inferred from catch blocks |
| Runtime metadata (timings, LLM calls) | Local `perf_counter()` + logging events only |
| State invariants | Basic validation in `validate_invariants()` |
| Stage transition rules | Defined in `VALID_TRANSITIONS` but not fully enforced |
| Request isolation | Per-request local variables only (OK) |
| TraceContext boundary | Contextvar-based, not in returned state |
| Serialization strategy | Ad-hoc dict construction in old code; `to_dict()` in new state |

---

## Current Error Handling Flow

```
SQLExtractor.extract()
    └─ LLM generate
    └─ _clean_sql()
    └─ SQLValidator.validate() → SQLValidationError / SQLSecurityError
        └─ Retry loop (max 2 corrections)
DatabaseConnection.execute_query()
    └─ Security validation (AST) → SQLSecurityError
    └─ Connection/Execution → DatabaseError (typed)
Agent.ask() catch blocks:
    └─ SQLValidationError/SQLSecurityError → error_code="SQL_VALIDATION_ERROR"
    └─ QueryExecutionError → error_code from DatabaseErrorCode
    └─ Exception → "Internal error."
```

---

## Integration Points for AgentState

**Required**: Wire `AgentState` into `TextToSQLAgent.ask()` as the internal carrier, while keeping the return dict format for API/CLI compatibility.

**Already Implemented in `src/agents/state.py`**:
- Complete typed state model with frozen sub-models
- Transition validation via `VALID_TRANSITIONS`
- Convenience methods for current architecture (`with_generated_sql`, `with_validation_passed`, etc.)
- Error state construction from exceptions
- Safe serialization with sensitive data redaction
- State invariants validation
- Backward-compatible API response conversion

**Partially Integrated in `TextToSQLAgent.ask()`**:
- State creation via `create_initial_state()`
- Session handling updates state
- SQL generation updates state
- Execution results update state
- Answer generation updates state
- Error paths update state
- Response conversion via `state_to_api_response()`

---

## Current Problems Found

### 1. Shared Mutable State Risk
- `TextToSQLAgent` instance is a global singleton (`agent_instance` in `routes.py`)
- `DatabaseConnection` creates fresh connections per query (fixed in Sprint 1)
- `MemoryManager` instance is per-agent but SQLite connections are per-operation
- AgentState is created fresh per request — **no cross-request contamination observed**

### 2. Unclear Ownership
- `trace_id` lives in `TraceContext` (contextvar) AND `RequestContext` — dual ownership
- `session_id` managed by `MemoryManager`, `RequestContext`, AND `TraceContext`
- `question` in `RequestContext` AND passed as parameter to methods
- `history` in `ConversationContext` AND fetched from `MemoryManager`

### 3. Mixed Infrastructure/Business State
- `AgentState` correctly avoids infrastructure objects (no DB connections, LLM clients, MemoryManager)
- But `TextToSQLAgent` holds infrastructure references (`self.db`, `self.llm`, `self.memory`, `self.extractor`)
- `TraceContext` is infrastructure correlation; `RequestContext` is business identity — boundary exists but could be clearer

### 4. State Duplication
- `session_id` in: `RequestContext`, `TraceContext`, `MemoryManager` session table
- `trace_id` in: `TraceContext`, `RequestContext`
- `question` in: `RequestContext`, method parameter
- `history` in: `ConversationContext`, fetched from `MemoryManager` each request

### 5. Implicit State
- Pipeline stage transitions are implicit in old flow, explicit in new `AgentState` but not fully used
- Validation status implicit in exception flow, explicit in `SQLState.validation_status`
- Execution status implicit in exception flow, explicit in `ExecutionState.status`

### 6. Incomplete Transition Enforcement
- `AgentState.transition_to()` validates but is not called consistently in all paths
- Some error paths create new `ErrorState` without transitioning pipeline stage
- `with_error_response()` transitions to `FAILED` but other methods may not

---

## TraceContext Boundary Analysis

**Current**: `TraceContext` (contextvar) carries `trace_id` and `session_id` for logging correlation.
**AgentState**: `RequestContext` carries `trace_id`, `session_id`, `request_id`, `question`, `timestamp`.

**Boundary Issue**:
- `TraceContext` = infrastructure/request correlation (logging, observability)
- `RequestContext` = business request identity
- They overlap on `trace_id` and `session_id`
- `AgentState` references `trace_id` from `TraceContext` at creation time

**Recommendation**:
- Keep `TraceContext` as pure infrastructure (logging correlation only)
- `AgentState.request.trace_id` references `TraceContext.trace_id` at creation
- `AgentState` should not depend on `TraceContext` after creation
- `session_id` in `TraceContext` is for logging convenience only; `RequestContext.session_id` is authoritative

---

## Memory Boundary Analysis

**Current**: `MemoryManager` provides `create_session()`, `get_history()`, `add_message()`, `session_exists()`, `clear_session()`.

**AgentState Interaction**:
- `ConversationContext.history` populated from `MemoryManager.get_history()` at request start
- `ConversationContext` does NOT store `MemoryManager` instance or SQLite connection
- New messages added to `MemoryManager` at request end (outside `AgentState`)

**Boundary Issue**:
- `AgentState` correctly has no dependency on `MemoryManager` implementation
- But `TextToSQLAgent` calls `MemoryManager` directly before/after state updates
- Future `MemoryManager` redesign (ADR-009) should not affect `AgentState` structure

**Recommendation**:
- `ConversationContext` remains a pure data snapshot (tuple of dicts)
- `MemoryManager` interaction stays in `TextToSQLAgent` orchestration layer
- `AgentState` never holds `MemoryManager` reference

---

## SQL State Analysis

**Current `SQLState` fields**:
- `generated_sql`: str | None
- `sql_fingerprint`: str | None (SHA256 of normalized SQL)
- `validation_status`: ValidationStatus (PENDING/PASSED/FAILED/SECURITY_REJECTED)
- `validation_message`: str | None
- `validation_reason_code`: str | None
- `correction_attempt`: int
- `max_correction_attempts`: int = 2

**Missing for Target Architecture**:
- `normalized_sql`: str | None (post-validation canonical form)
- `parsed_ast`: Any (sqlglot AST for semantic validation)
- `security_decision`: SecurityDecision | None (structured, not just reason_code)
- `semantic_validation_status`: ValidationStatus | None
- `complexity_score`: float | None

**What Should NOT Be in SQLState**:
- `SQLSecurityValidator` instance
- `SQLValidator` instance
- Raw exception objects
- Database connection/cursor

---

## Execution State Analysis

**Current `ExecutionState` fields**:
- `status`: ExecutionStatus (PENDING/SUCCESS/FAILED/ROW_LIMIT_EXCEEDED/TIMEOUT)
- `rows`: tuple[dict, ...]
- `row_count`: int
- `duration_ms`: float | None
- `error_code`: str | None
- `error_message`: str | None
- `database_error_code`: str | None

**Missing for Target Architecture**:
- `columns`: list[str] | None (schema of result)
- `truncated`: bool (whether row limit was hit)
- `query_plan_hash`: str | None (for caching)
- `execution_id`: str | None (for tracing)

**What Should NOT Be in ExecutionState**:
- Database connection
- Cursor
- Transaction handle
- pymssql driver objects
- Raw exception objects

---

## Response State Analysis

**Current `ResponseState` fields**:
- `answer`: str | None
- `success`: bool
- `error`: str | None
- `metadata`: dict[str, Any]

**Missing for Target Architecture**:
- `presentation_plan`: PresentationPlan | None
- `result_profile`: ResultProfile | None
- `validation_result`: AnswerValidationResult | None
- `citations`: list[str] (source attribution)

**What Should NOT Be in ResponseState**:
- UI-specific objects (DataFrames, HTML, Streamlit components)
- Raw LLM response
- Formatting logic

---

## Error State Analysis

**Current `ErrorState` fields**:
- `category`: ErrorCategory | None
- `code`: str | None
- `message`: str | None
- `stage`: PipelineStage | None
- `retryable`: bool
- `details`: dict[str, Any]

**Missing for Target Architecture**:
- `trace_reference`: str | None (link to TraceContext for debugging)
- `user_facing_message`: str | None (separate from internal message)
- `recovery_suggestions`: list[str] | None

**What Should NOT Be in ErrorState**:
- Raw Exception objects
- Stack traces (belongs in logs only)
- Internal implementation details

---

## Runtime Metadata Analysis

**Current `RuntimeMetadata` fields**:
- `stage_timings`: dict[str, float]
- `stage_start_times`: dict[str, float]
- `llm_calls`: int
- `llm_total_duration_ms`: float
- `model_provider`: str | None
- `model_name`: str | None
- `attempt_counters`: dict[str, int]

**Missing for Target Architecture**:
- `token_usage`: dict[str, int] (prompt/completion/total)
- `cost_estimate`: float | None
- `cache_hits`: int
- `validator_calls`: int

**Separation Concern**:
- `RuntimeMetadata` = observability/telemetry (can be logged, sampled)
- Business state (`SQLState`, `ExecutionState`, etc.) = application processing state
- They are correctly separated in current design

---

## Future Components Compatibility

The current `AgentState` includes placeholder fields for future components:
- `question_analysis`: QuestionSpec (ADR-001)
- `schema_context`: SchemaContext (ADR-002)
- `query_plan`: QueryPlan (ADR-003)
- `result_profile`: ResultProfile (ADR-007)
- `presentation_plan`: PresentationPlan (ADR-008)

These are typed as `Optional[Any]` currently — will need proper typing when components are implemented.

---

## Type System Decision (Current)

**Current Choice**: Python `dataclass` with `frozen=True` for sub-models, mutable `AgentState` with `replace()`.

**Rationale**:
- Standard library, no external dependency
- `frozen=True` provides immutability guarantees for sub-state
- `dataclasses.replace()` enables functional update pattern
- Compatible with existing codebase conventions (used in `SecurityDecision`, `ExecutionPolicy`)
- No Pydantic dependency for core state model (Pydantic used only at API boundary)

**Trade-offs**:
- No built-in validation (manual in `validate_invariants()`)
- No automatic serialization (custom `to_dict()`)
- No schema generation for OpenAPI (API uses Pydantic models separately)

---

## Request Isolation Design

**Current**: Each `ask()` call creates fresh `AgentState` via `create_initial_state()`.
- No global mutable state in `AgentState`
- `TextToSQLAgent` singleton holds infrastructure, not request state
- `TraceContext` uses `contextvars` for request isolation
- `MemoryManager` uses per-operation SQLite connections

**Risk**:
- `agent_instance` global singleton — if it ever stored request state, would be problematic
- Currently it only stores infrastructure (`db`, `llm`, `extractor`, `memory`, `prompts`)
- **No cross-request state leakage observed**

---

## Sensitive Data Analysis

**Potentially Sensitive Fields in AgentState**:
| Field | Location | Sensitivity | Current Handling |
|-------|----------|-------------|------------------|
| `question` | `RequestContext.question` | User input, may contain PII | Redacted in `to_dict(include_sensitive=False)` |
| `generated_sql` | `SQLState.generated_sql` | May reveal schema, data patterns | Redacted in safe serialization |
| `rows` | `ExecutionState.rows` | Actual data, may contain PII | Redacted in safe serialization |
| `answer` | `ResponseState.answer` | May contain data from rows | Redacted in safe serialization |
| `trace_id` | `RequestContext.trace_id` | Correlation ID, not sensitive | Included in safe serialization |
| `session_id` | `RequestContext.session_id` | Correlation ID, not sensitive | Included in safe serialization |

**Logger Redaction**: `sanitize()` in `logger.py` redacts:
- Keys matching password/token/secret patterns
- Keys in `_PRIVATE_PAYLOAD_KEYS`: `sql`, `question`, `answer`, `prompt`, `response`, `rows`, `results`
- Secret-like patterns in string values

---

## Safe Representation

**Current**: `AgentState.__repr__()` and `__str__()` delegate to `safe_repr()` which returns:
```
AgentStage(request_id=abc12345, stage=sql_generated, success=False)
```

No sensitive data in default representation. `to_dict(include_sensitive=False)` redacts payload fields.

---

## Dependency Boundary Analysis

**AgentState Dependencies** (from `src/agents/state.py` imports):
- `dataclasses`, `datetime`, `enum`, `typing`, `uuid` — stdlib only
- `src.utils.logger.sql_fingerprint` — utility function (could be inlined)

**AgentState Does NOT Depend On**:
- FastAPI / Pydantic (API boundary separate)
- Streamlit (UI boundary separate)
- pymssql / SQLAlchemy (database boundary separate)
- OpenAI SDK / other LLM SDKs (LLM boundary separate)
- SQLite / MemoryManager (memory boundary separate)
- SQLGlot / security validator (validation boundary separate)

**Good**: Clean dependency boundary maintained.

---

## Target File Placement

**Current**: `src/agents/state.py`

**Architectural Layer**: `agents` (application/service layer)

**Assessment**:
- `AgentState` is the central state model for the agent orchestration
- It belongs in `src/agents/` as it's specific to the `TextToSQLAgent` pipeline
- Not in `core/` because it's not a shared kernel primitive
- Not in `api/` because it's not an API contract (API uses `AskRequest`/`AskResponse`)

**Recommendation**: Keep at `src/agents/state.py` — correct location.

---

## Scope for This Prompt (1A)

**In Scope**:
- Complete audit of current state flow (this document)
- Design target AgentState architecture (next document)
- Define contracts, invariants, boundaries
- Document all decisions

**Out of Scope**:
- Implementation/wiring (Prompt 1B)
- New orchestrator (Prompt 4)
- QuestionAnalyzer, SchemaCatalog, QueryPlanner, etc. (future sprints)
- Memory redesign (ADR-009, Sprint 11)
- Persistence of AgentState
- API contract changes

---

## Files to Create/Update in This Prompt

1. `docs/audit/current-agent-state.md` — **This file** (updated)
2. `docs/architecture/agent-state-design.md` — Target design
3. `docs/contracts/agent-state.md` — Formal contract
4. `docs/sprints/sprint-2-prompt-1a-report.md` — Final report