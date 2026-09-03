# Current Agent State Integration Audit

**Date**: 2026-08-29
**Sprint**: 2 — Prompt 1C
**Status**: Complete audit for Sprint 2 Prompt 1C

---

## Current Integration Architecture

The `AgentState` is now fully wired into `TextToSQLAgent.ask()` as the internal state carrier. All paths (success and error) flow through `state_to_api_response()` for the external API contract.

### Integration Flow

```
Request (API/CLI)
    ↓
create_initial_state() → AgentState(INITIALIZED)
    ↓
Session resolution → state.request.with_session()
    ↓
History fetch → state.conversation.with_history()
    ↓
SQL Generation → state.with_generated_sql() → SQL_GENERATED
    ↓
Validation (implicit via execute_query security check) → state.with_validation_passed() → VALIDATED
    ↓
Execution → state.with_execution_results() → EXECUTED
    ↓
Answer Generation → state.with_answer() → ANSWERED
    ↓
state_to_api_response() → API Response
```

### Error Paths (All use State)

1. **SQLValidationError / SQLSecurityError** → `state.with_validation_failed()` or `state.with_security_rejected()` → FAILED
2. **QueryExecutionError** → `state.with_execution_error()` → FAILED
3. **Generic Exception** → `state.with_error_response()` → FAILED

All three error handlers now consistently use `state_to_api_response(state)`.

---

## Where State is Created

**Location**: `src/agents/text_to_sql_agent.py:98-102`

```python
state = create_initial_state(
    question=question,
    session_id=session_id,
    trace_id=trace_id,
)
```

**Factory**: `src/agents/state.py:668-684` - `create_initial_state()`

Creates fresh `RequestContext`, `ConversationContext`, and `AgentState` with all sub-models at defaults. Called once per `ask()` invocation.

---

## Where State is Updated

| Operation | Method | Stage Transition |
|-----------|--------|------------------|
| Session assignment | `replace(state, request=state.request.with_session(...))` | None |
| History loading | `replace(state, conversation=state.conversation.with_history(...))` | None |
| SQL Generation | `state.with_generated_sql(sql)` | INITIALIZED → SQL_GENERATED |
| Validation Pass | `state.with_validation_passed()` | SQL_GENERATED → VALIDATED |
| Validation Fail | `state.with_validation_failed(...)` | * → FAILED |
| Security Reject | `state.with_security_rejected(...)` | * → FAILED |
| Execution Success | `state.with_execution_results(rows, duration)` | VALIDATED → EXECUTED |
| Execution Error | `state.with_execution_error(...)` | * → FAILED |
| Answer Generation | `state.with_answer(answer)` | EXECUTED → ANSWERED |
| Generic Error | `state.with_error_response(...)` | * → FAILED |
| LLM Call Timing | `state.with_llm_call(duration, ...)` | None |
| Stage Timing | `state.with_stage_timing(...)` | None |

All updates return new `AgentState` instances via `dataclasses.replace()`.

---

## Paths That Avoid State

**None** - All code paths in `TextToSQLAgent.ask()` now use `AgentState`:
- Happy path: Creates state, updates through pipeline, returns via `state_to_api_response()`
- Validation error path: Updates state, returns via `state_to_api_response()`
- Security error path: Updates state, returns via `state_to_api_response()`
- Execution error path: Updates state, returns via `state_to_api_response()`
- Generic error path: Updates state, returns via `state_to_api_response()`

Previous raw-dict fallbacks in error handlers (Prompt 1A findings) were removed in Prompt 1B.

---

## Ad-hoc Dictionary Usage

**None in Agent core** - The only remaining ad-hoc dict construction is:
- `src/api/routes.py:49-57` - API-layer fallback for unhandled exceptions (creates `AskResponse` directly)
- This is appropriate: API boundary handles catastrophic failures outside Agent

---

## State Initialization Per Request

**Verified**: `create_initial_state()` is called at line 98 of `TextToSQLAgent.ask()`, inside the method, before any processing. Each `ask()` call creates a new `AgentState`.

No class-level or global `AgentState` exists.

---

## State Persistence Across Requests

**Verified**: No persistence mechanism exists for `AgentState`:
- `TextToSQLAgent` singleton holds only infrastructure: `self.db`, `self.llm`, `self.extractor`, `self.memory`, `self.prompts`
- `AgentState` is a local variable in `ask()`
- `TraceContext` uses `contextvars` - request-scoped by design
- `MemoryManager` uses per-operation SQLite connections

---

## Nested Mutability Inspection

All sub-models are `@dataclass(frozen=True)`:
- `RequestContext` - frozen
- `ConversationContext` - frozen
- `SQLState` - frozen
- `ExecutionState` - frozen
- `ResponseState` - frozen
- `ErrorState` - frozen
- `RuntimeMetadata` - frozen

Default factories used for mutable defaults:
```python
conversation: ConversationContext = field(default_factory=ConversationContext)
sql: SQLState = field(default_factory=SQLState)
execution: ExecutionState = field(default_factory=ExecutionState)
response: ResponseState = field(default_factory=ResponseState)
error: ErrorState = field(default_factory=ErrorState)
metadata: RuntimeMetadata = field(default_factory=RuntimeMetadata)
```

All `list`, `dict`, `set` fields in frozen models use immutable types (`tuple`, `frozenset`) or `default_factory`.

---

## TraceContext Integration

**Boundary**: `TraceContext` (infrastructure) ↔ `AgentState.request` (business)

- `TraceContext` created by FastAPI middleware or CLI entry point
- `trace_id` passed to `create_initial_state()` → stored in `RequestContext.trace_id`
- After creation, `AgentState` has no dependency on `TraceContext`
- `AgentState.request.trace_id` used for logging correlation
- `TraceContext` updated with `session_id` via `update_session_id()` for logging convenience

---

## Trace ID Consistency

For one request:
- `TraceContext.trace_id` (contextvar) = `AgentState.request.trace_id`
- Used in all `log_event()` calls via `trace_id=state.request.trace_id`
- Returned in API response header `X-Trace-ID`
- Consistent across: AgentState, Observability events, API response

---

## Memory Boundary

**Verified**: `AgentState` does NOT contain:
- `MemoryManager` instance
- SQLite connection/cursor
- Database handle
- Persistence service

`AgentState.conversation` holds immutable `tuple[dict]` history snapshot only.
`TextToSQLAgent` calls `MemoryManager` before/after state updates explicitly.

---

## Conversation Context

`ConversationContext` fields:
- `history: tuple[dict[str, str], ...]` - immutable snapshot from `MemoryManager.get_history()`
- `resolved_references: dict` - placeholder for future QuestionAnalyzer
- `active_filters: dict` - placeholder for future QuestionAnalyzer

Request-scoped, non-owning, no persistence dependency.

---

## Security Boundary

**Verified**: SQL flow remains:
```
LLM SQL
    ↓
DatabaseConnection.execute_query()
    ↓
SQLSecurityValidator.validate()  (AST policy engine)
    ↓
SecurityDecision (valid/rejected)
    ↓
If rejected: raise SQLSecurityError → Agent catches → state.with_security_rejected()
    ↓
If passed: execute query
```

`AgentState.sql` only records outcome (`validation_status`, `validation_reason_code`). No validator instance in State.

---

## Safe Execution Boundary

**Verified**: `AgentState` does NOT:
- Execute SQL directly
- Hold database connections/cursors
- Bypass `ExecutionPolicy` (row limits, timeouts)
- Handle typed DB errors (captured in `ExecutionState`)

`DatabaseConnection.execute_query()` enforces all policies. `ExecutionState` records result snapshot.

---

## Observability Boundary

**Verified**: State updates produce observability events:
- `agent_started` / `agent_completed` / `agent_failed`
- `sql_generated` (with fingerprint only)
- `sql_validation_started` / `sql_validation_passed` / `sql_validation_failed`
- `db_execution_started` / `db_execution_completed` / `db_execution_failed` / `db_timeout`
- `llm_call_started` / `llm_call_completed` / `llm_call_failed`
- `response_generated`

All events include `trace_id=state.request.trace_id`. `RuntimeMetadata` captures structured telemetry.

---

## API Compatibility

**Verified**: `state_to_api_response()` produces exact backward-compatible format:
- Success: `{"success": True, "question", "answer", "sql", "row_count", "rows", "session_id"}`
- Failure: `{"success": False, "question", "error", "sql", "session_id", "database_error_code" (optional)}`

`AskRequest` / `AskResponse` Pydantic models unchanged.

---

## CLI Compatibility

**Verified**: `run.py` and `run_ui.py` consume `agent.ask()` return dict - unchanged format.

---

## Evaluation Adapter Compatibility

**Verified**: `EvaluationAgentAdapter.normalize_response()` expects the same dict format from `agent.ask()`. Works unchanged.

---

## Error Path Audit

| Error Type | Handler | State Method | Stage | Response Contract |
|------------|---------|--------------|-------|-------------------|
| SQLValidationError | except block | `with_validation_failed()` | FAILED | ✅ state_to_api_response |
| SQLSecurityError | except block | `with_security_rejected()` | FAILED | ✅ state_to_api_response |
| QueryExecutionError | except block | `with_execution_error()` | FAILED | ✅ state_to_api_response |
| Generic Exception | except block | `with_error_response()` | FAILED | ✅ state_to_api_response |

All error paths transition to `FAILED` and populate `ErrorState`.

---

## State Transition Audit

All transitions use convenience methods that chain `transition_to()`:

| Method | From Stage | To Stage | Validated |
|--------|------------|----------|-----------|
| `with_generated_sql()` | INITIALIZED | SQL_GENERATED | ✅ |
| `with_validation_passed()` | SQL_GENERATED | VALIDATED | ✅ |
| `with_validation_failed()` | * | FAILED | ✅ |
| `with_security_rejected()` | * | FAILED | ✅ |
| `with_execution_results()` | VALIDATED | EXECUTED | ✅ |
| `with_execution_error()` | * | FAILED | ✅ |
| `with_answer()` | EXECUTED | ANSWERED | ✅ |
| `with_error_response()` | * | FAILED | ✅ |

Direct `state.pipeline_stage = ...` assignments: **None found** in `TextToSQLAgent.ask()`.

Compatibility shortcuts preserved:
- INITIALIZED → SQL_GENERATED (current architecture)
- EXECUTED → ANSWERED (current architecture)

---

## State Invariants

`validate_invariants()` checks 10 rules (INV-001 through INV-010). Called in tests, not automatically in production flow. All existing tests pass.

---

## Serialization Boundary

Three representations verified distinct:

1. **Internal State** (`AgentState` object) - Full typed model, all fields accessible
2. **Safe Serialization** (`state.to_dict(include_sensitive=False)`) - Redacts: question, generated_sql, normalized_sql, rows, answer
3. **API Response** (`state_to_api_response()`) - Plain text for authorized API consumers

No substitution possible - different return types and purposes.

---

## Safe Representation

`__repr__()` and `__str__()` delegate to `safe_repr()`:
```
AgentStage(request_id=abc12345, stage=sql_generated, success=False)
```

No sensitive data in default representation.

---

## Summary

The AgentState integration is **complete and correct**:
- Request-scoped State creation
- No shared/global State
- Frozen sub-models prevent nested mutability leaks
- All paths use State consistently
- TraceContext boundary preserved
- Memory boundary preserved
- Security/Execution boundaries intact
- Observability events produced
- API/CLI/Evaluation compatibility maintained
- Error paths consistent
- Transitions validated
- Serialization boundaries enforced
- Safe representation protects sensitive data