# AgentState Contract

**Component**: `AgentState` (src/agents/state.py)
**Version**: 1.0
**Status**: Design complete

---

## Purpose

`AgentState` is the **single source of truth** for all data flowing through the Text-to-SQL pipeline. It carries request identity, conversation context, pipeline stage, stage-specific data (SQL, execution, response), errors, and observability metadata. It is a pure data model — no business logic, no external dependencies, no infrastructure resources.

---

## Interface

### Factory
```python
def create_initial_state(
    question: str,
    session_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    history: Optional[list[dict[str, str]]] = None,
) -> AgentState
```
Creates initial `AgentState` at `PipelineStage.INITIALIZED`.

### Core Transition Methods
```python
# Pipeline control
def transition_to(self, new_stage: PipelineStage) -> AgentState
def can_transition_to(self, new_stage: PipelineStage) -> bool

# SQL generation
def with_generated_sql(self, sql: str) -> AgentState
def with_correction_attempt(self, sql: str) -> AgentState

# Validation
def with_validation_passed(self) -> AgentState
def with_validation_failed(self, message: str, reason_code: Optional[str] = None) -> AgentState
def with_security_rejected(self, reason_code: str, message: str) -> AgentState

# Execution
def with_execution_results(self, rows: list[dict], duration_ms: float) -> AgentState
def with_execution_error(
    self,
    status: ExecutionStatus,
    error_code: str,
    error_message: str,
    database_error_code: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> AgentState

# Response
def with_answer(self, answer: str, metadata: Optional[dict] = None) -> AgentState
def with_error_response(
    self,
    error: str,
    category: ErrorCategory = ErrorCategory.INTERNAL,
    code: Optional[str] = None,
    stage: Optional[PipelineStage] = None,
    retryable: bool = False,
    metadata: Optional[dict] = None,
) -> AgentState

# Metadata
def with_stage_timing(self, stage: str, duration_ms: float) -> AgentState
def with_llm_call(self, duration_ms: float, provider: Optional[str] = None, model: Optional[str] = None) -> AgentState
def increment_attempt(self, operation: str) -> AgentState

# Inspection
def validate_invariants(self) -> list[str]
def is_terminal(self) -> bool
def is_failed(self) -> bool
def is_success(self) -> bool
```

### Serialization
```python
def to_dict(self, *, include_sensitive: bool = False) -> dict[str, Any]
def safe_repr(self) -> str
```

### API Compatibility
```python
def state_to_api_response(state: AgentState) -> dict[str, Any]
```
Returns backward-compatible response dict for `/api/v1/ask`, CLI, and Streamlit.

---

## Inputs

### create_initial_state
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| question | str | Yes | User's natural language question |
| session_id | str? | No | Existing session ID; created if absent |
| trace_id | str? | No | Trace ID from TraceContext; generated if absent |
| history | list[dict]? | No | Conversation history from MemoryManager |

### Transition Methods
Each method takes only the data it needs to record (e.g., `with_generated_sql(sql)` takes the SQL string). No infrastructure objects, no service references.

---

## Outputs

### AgentState (Internal)
Complete typed state with all sub-models populated per pipeline progress.

### state_to_api_response (External)
```python
# Success
{
    "success": True,
    "question": str,
    "answer": str,
    "sql": str,
    "row_count": int,
    "rows": list[dict],
    "session_id": str,
}

# Failure
{
    "success": False,
    "question": str,
    "error": str,
    "sql": str | None,
    "session_id": str,
    "database_error_code": str | None,  # optional
}
```

---

## Errors

`AgentState` itself does not raise errors for business logic. Transition validation raises `ValueError` for invalid stage transitions (programming error).

Errors from pipeline components are **recorded in state**, not raised:
- `SQLState.validation_status = FAILED/SECURITY_REJECTED`
- `ExecutionState.status = FAILED/ROW_LIMIT_EXCEEDED/TIMEOUT`
- `ErrorState` populated with category, code, message, stage, retryable
- `ResponseState.success = False`, `ResponseState.error = user-facing message`

---

## Invariants

| ID | Rule |
|----|------|
| INV-001 | `execution.status == SUCCESS ∧ row_count > 0 → rows ≠ ∅` |
| INV-002 | `sql.is_security_rejected → execution.status == PENDING` |
| INV-003 | `response.success → response.answer ≠ None` |
| INV-004 | `¬response.success → response.error ≠ None` |
| INV-005 | `pipeline_stage == FAILED → error.category ≠ None` |
| INV-006 | `sql.validation_status == PASSED → sql.generated_sql ≠ None` |
| INV-007 | `sql.correction_attempt > 0 → sql.generated_sql ≠ None` |
| INV-008 | `metadata.llm_calls ≥ 0` |
| INV-009 | `sql.correction_attempt ≤ sql.max_correction_attempts` |
| INV-010 | `execution.truncated → execution.status == ROW_LIMIT_EXCEEDED` |

**Enforcement**: `validate_invariants()` returns list of violations (empty if valid). Called at transitions and before serialization. Violations logged as warnings.

---

## Non-Responsibilities

`AgentState` does NOT:
- Generate SQL
- Validate SQL
- Call databases
- Call LLMs
- Build prompts
- Manage memory/sessions
- Analyze results
- Render presentations
- Handle HTTP requests
- Manage connections/cursors
- Hold infrastructure references (DB, LLM, Memory, Security)

---

## State Machine

```
INITIALIZED
    → QUESTION_ANALYZED (future)
    → SQL_GENERATED (current)
    → FAILED

QUESTION_ANALYZED → SCHEMA_RESOLVED → PLAN_CREATED → SQL_GENERATED → FAILED

SQL_GENERATED → VALIDATED → FAILED

VALIDATED → EXECUTED → FAILED

EXECUTED → RESULT_ANALYZED (future)
       → ANSWERED (current)
       → FAILED

RESULT_ANALYZED → ANSWERED → FAILED

ANSWERED (terminal success)
FAILED (terminal failure)
```

---

## Data Model Summary

### RequestContext (Frozen)
- `request_id`: str — Unique per request
- `trace_id`: str — Correlation ID from TraceContext
- `session_id`: str? — Conversation session
- `question`: str — User question (sensitive)
- `timestamp`: datetime — Request creation time

### ConversationContext (Frozen)
- `history`: tuple[dict] — Last N messages (role, content)
- `resolved_references`: dict — Future: resolved pronouns/entities
- `active_filters`: dict — Future: implicit filters from context

### PipelineStage (Enum)
`INITIALIZED | QUESTION_ANALYZED | SCHEMA_RESOLVED | PLAN_CREATED | SQL_GENERATED | VALIDATED | EXECUTED | RESULT_ANALYZED | ANSWERED | FAILED`

### SQLState (Frozen)
- `generated_sql`: str? — Raw SQL from generator
- `sql_fingerprint`: str? — SHA256 of normalized SQL
- `normalized_sql`: str? — Future: canonical form after validation
- `validation_status`: ValidationStatus — `PENDING | PASSED | FAILED | SECURITY_REJECTED`
- `validation_message`: str? — Validator message
- `validation_reason_code`: str? — Machine-readable reason
- `security_decision`: SecurityDecision? — Future: full decision object
- `semantic_status`: ValidationStatus? — Future: semantic validation result
- `correction_attempt`: int — Current retry count
- `max_correction_attempts`: int — Configurable limit (default 2)

### ExecutionState (Frozen)
- `status`: ExecutionStatus — `PENDING | SUCCESS | FAILED | ROW_LIMIT_EXCEEDED | TIMEOUT`
- `rows`: tuple[dict] — Result rows (sensitive)
- `row_count`: int — Number of rows
- `columns`: tuple[str] — Column names
- `duration_ms`: float? — Execution time
- `error_code`: str? — Application error code
- `error_message`: str? — Error description
- `database_error_code`: str? — Driver error code
- `truncated`: bool — Row limit hit

### ResponseState (Frozen)
- `answer`: str? — Natural language answer (sensitive)
- `success`: bool — Overall success
- `error`: str? — User-facing error message
- `metadata`: dict — Presentation metadata
- `presentation_plan`: PresentationPlan? — Future

### ErrorState (Frozen)
- `category`: ErrorCategory? — `QUESTION | SCHEMA | PLANNING | SQL_GENERATION | SECURITY | VALIDATION | EXECUTION | RESPONSE | INTERNAL`
- `code`: str? — Machine-readable error code
- `message`: str? — Internal error message
- `user_facing_message`: str? — Safe for UI display
- `stage`: PipelineStage? — Where error occurred
- `retryable`: bool — Whether retry may succeed
- `details`: dict — Additional context
- `trace_reference`: str? — Link to trace for debugging

### RuntimeMetadata (Frozen)
- `stage_timings`: dict[str, float] — Stage durations (ms)
- `stage_start_times`: dict[str, float] — Active stage start times (internal)
- `llm_calls`: int — Total LLM calls
- `llm_total_duration_ms`: float — Total LLM time
- `model_provider`: str? — Provider name
- `model_name`: str? — Model name
- `token_usage`: dict[str, int] — Future: prompt/completion/total tokens
- `attempt_counters`: dict[str, int] — Retry counters per operation
- `validator_calls`: int — Validation attempts

---

## Boundaries

### TraceContext
- **AgentState** holds `trace_id` as reference (copied at creation)
- **TraceContext** (contextvar) handles logging correlation
- No mutual dependency after initialization

### MemoryManager
- **AgentState.conversation** holds immutable history snapshot
- **MemoryManager** called by orchestrator before/after state updates
- No reference to MemoryManager in AgentState

### DatabaseConnection
- **AgentState.execution** holds immutable result snapshot
- **DatabaseConnection** creates fresh connection per query
- No connection/cursor in AgentState

### SecurityValidator
- **AgentState.sql** holds validation outcome (status, reason, message)
- **SecurityValidator** is stateless policy engine
- No validator instance in AgentState

### Observability
- **RuntimeMetadata** in AgentState for structured telemetry
- **Logger/TraceContext** external, consumes `to_dict(include_sensitive=False)`
- No circular dependency

---

## Concurrency

- `AgentState` created per request → no shared state
- All sub-models frozen → thread-safe reads
- `AgentState` methods return new instances → no in-place mutation
- Safe for concurrent requests through same `TextToSQLAgent` singleton

---

## Serialization Contract

### to_dict(include_sensitive=False) — Safe for Logging
- Redacts: `question`, `generated_sql`, `normalized_sql`, `rows`, `answer`
- Includes: IDs, counts, statuses, timings, fingerprints, metadata

### to_dict(include_sensitive=True) — Full State
- All fields plain
- For debugging only, explicit opt-in

### state_to_api_response — API Contract
- Exact backward-compatible format
- No redaction (API layer handles authorization)

---

## Versioning

- State structure versioned via `PipelineStage` enum and field presence
- New fields added as `Optional` with defaults
- `state_to_api_response` maintains v1 API format
- Future: add `state_version` field if breaking changes needed

---

## Testing Requirements

| Test | Description |
|------|-------------|
| `test_create_initial_state` | Factory produces valid INITIALIZED state |
| `test_transition_validation` | Valid transitions succeed, invalid raise ValueError |
| `test_sql_generation_flow` | INITIALIZED → SQL_GENERATED → VALIDATED |
| `test_execution_flow` | VALIDATED → EXECUTED → ANSWERED |
| `test_error_flows` | Each error path populates ErrorState + FAILED stage |
| `test_invariants` | All INV-001 through INV-010 validated |
| `test_serialization_safe` | `to_dict(False)` redacts sensitive fields |
| `test_serialization_full` | `to_dict(True)` includes all fields |
| `test_request_isolation` | Concurrent requests produce independent states |
| `test_api_response_compat` | `state_to_api_response` matches current format |