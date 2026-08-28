# Current Agent State Flow Audit

**Date**: 2026-08-28
**Sprint**: 2 — Prompt 1
**Status**: Pre-integration audit

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
| State invariants | None enforced |
| Stage transition rules | None (linear flow) |
| Request isolation | Per-request local variables only (OK) |
| TraceContext boundary | Contextvar-based, not in returned state |
| Serialization strategy | Ad-hoc dict construction |

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

The `src/agents/state.py` already defines:
- `RequestContext` (request_id, trace_id, session_id, question)
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

**Required**: Wire `AgentState` into `TextToSQLAgent.ask()` as the internal carrier, while keeping the return dict format for API/CLI compatibility.

---

## Decision: Mutability Strategy

`AgentState` uses **frozen dataclasses** for all sub-models (`RequestContext`, `ConversationContext`, `SQLState`, `ExecutionState`, `ResponseState`, `ErrorState`, `RuntimeMetadata`) with `replace()` for transitions.

`AgentState` itself is **mutable** (not frozen) to allow method chaining, but all sub-state updates return new instances via `replace()`.

This provides:
- Predictable transitions
- No shared mutable state between requests
- Safe logging/repr (no sensitive data by default)

---

## Decision: Serialization Strategy

- `to_dict(include_sensitive=False)` → safe for logging/observability (redacts question, SQL, rows, answer)
- `to_dict(include_sensitive=True)` → full state for debugging (explicit opt-in)
- No automatic persistence in this sprint (out of scope)
- `state_to_api_response()` → backward-compatible API dict

---

## Scope for This Prompt

**In Scope**:
- Wire `AgentState` into `TextToSQLAgent.ask()` as internal carrier
- Replace ad-hoc dict construction with `state_to_api_response()`
- Preserve all existing API/CLI behavior
- Add AgentState unit tests (creation, transitions, invariants, serialization, isolation)
- Add sensitive-data redaction tests

**Out of Scope**:
- New orchestrator (Prompt 4)
- QuestionAnalyzer, SchemaCatalog, QueryPlanner, etc. (future sprints)
- Memory redesign (ADR-009)
- Persistence of AgentState