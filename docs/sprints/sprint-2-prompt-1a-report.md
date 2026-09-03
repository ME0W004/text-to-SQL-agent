# Sprint 2 — Prompt 1A Report: Agent State Audit & Design

**Date**: 2026-08-29
**Sprint**: 2 — Architecture Foundation
**Prompt**: 1A — Agent State Audit & Design
**Status**: Complete

---

## 1. Executive Summary

### Current State
The codebase already contains a **comprehensive `AgentState` implementation** in `src/agents/state.py` (701 lines) that was created during Sprint 1 but not fully wired into `TextToSQLAgent.ask()`. The current `TextToSQLAgent.ask()` method partially uses this state model but still constructs ad-hoc response dictionaries in some error paths.

The existing `AgentState` design is **well-architected**:
- Frozen dataclasses for all sub-models (immutability)
- Explicit `PipelineStage` enum with validated transitions
- Structured `SQLState`, `ExecutionState`, `ResponseState`, `ErrorState`
- `RuntimeMetadata` for observability separated from business state
- Safe serialization with sensitive data redaction
- State invariants validation
- Backward-compatible API response conversion

### Key Problems Found
1. **Incomplete Integration**: `TextToSQLAgent.ask()` uses `AgentState` for happy path but falls back to raw dicts in some error handlers
2. **Dual trace_id Ownership**: `TraceContext` (contextvar) and `RequestContext` both carry `trace_id` and `session_id` — boundary clear but duplicated
3. **Session ID Triplication**: `session_id` in `RequestContext`, `TraceContext`, and `MemoryManager` — orchestration layer manages this correctly but ownership is implicit
4. **Transition Enforcement Gaps**: Some error paths create `ErrorState` without transitioning `pipeline_stage` to `FAILED`
5. **Missing Fields for Target Architecture**: `normalized_sql`, `security_decision`, `columns`, `truncated`, `token_usage`, `presentation_plan` placeholders exist but typed as `Any`

### Target State Design
The design **extends and formalizes** the existing `AgentState` with:
- Additional fields for future components (QuestionAnalyzer, SchemaCatalog, QueryPlanner, etc.)
- Stricter transition enforcement via `transition_to()` in all paths
- Clear ownership model for each state component
- Explicit boundaries with TraceContext, MemoryManager, DatabaseConnection, SecurityValidator, Observability
- Complete invariant set (10 rules)
- Mutability model: frozen sub-models, mutable container with functional updates
- Type system: stdlib dataclass (no Pydantic in core)
- Serialization: dual-mode (safe/full) + API compatibility layer

---

## 2. Files Inspected

### Documentation
- `docs/sprints/sprint-0-report.md`
- `docs/sprints/sprint-1-report.md`
- `docs/architecture/current-architecture.md`
- `docs/architecture/current-components.md`
- `docs/architecture/current-data-flow.md`
- `docs/architecture/target-architecture.md`
- `docs/architecture/target-data-flow.md`
- `docs/architecture/component-responsibilities.md`
- `docs/contracts/component-contracts.md`
- `docs/adr/ADR-001` through `ADR-011`
- `docs/migration/migration-plan.md`
- `docs/audit/current-agent-state.md` (existing)
- `docs/audit/codebase-audit.md`
- `docs/audit/security-audit.md`
- `docs/audit/memory-audit.md`
- `docs/audit/output-audit.md`
- `docs/audit/observability-audit.md`
- `docs/audit/database-execution-audit.md`
- `docs/audit/prompt-audit.md`
- `docs/audit/dependency-coupling.md`

### Source Code
- `src/agents/text_to_sql_agent.py` (278 lines)
- `src/agents/state.py` (701 lines) — **Primary focus**
- `src/api/routes.py` (77 lines)
- `src/api/models.py` (23 lines)
- `src/api/main.py`
- `src/core/sql_extractor/extractor.py` (93 lines)
- `src/core/sql_extractor/validator.py` (27 lines)
- `src/core/security/sql_security.py` (176 lines)
- `src/core/database/connection.py` (274 lines)
- `src/core/database/models.py` (139 lines)
- `src/core/prompts.py` (108 lines)
- `src/core/llm/client.py` (51 lines)
- `src/memory/manager.py` (86 lines)
- `src/utils/logger.py` (178 lines)
- `src/utils/config_loader.py` (44 lines)
- `src/utils/exceptions.py` (25 lines)
- `run.py` (42 lines)
- `run_api.py` (11 lines)
- `run_ui.py` (14 lines)
- `src/web/app.py` (336 lines)
- `evaluation/adapters.py` (147 lines)
- `evaluation/models.py` (88 lines)
- `evaluation/runner.py` (230 lines)

---

## 3. Current State Findings

### State Objects Identified

| Object | Location | Type | Scope | Owner |
|--------|----------|------|-------|-------|
| `AgentState` | `src/agents/state.py` | dataclass | Request | Orchestrator |
| `RequestContext` | `src/agents/state.py` | frozen dataclass | Request | Request |
| `ConversationContext` | `src/agents/state.py` | frozen dataclass | Request | Memory |
| `PipelineStage` | `src/agents/state.py` | Enum | Request | Orchestrator |
| `SQLState` | `src/agents/state.py` | frozen dataclass | Request | Generator/Validator |
| `ExecutionState` | `src/agents/state.py` | frozen dataclass | Request | Executor |
| `ResponseState` | `src/agents/state.py` | frozen dataclass | Request | AnswerGen/Presenter |
| `ErrorState` | `src/agents/state.py` | frozen dataclass | Request | Pipeline |
| `RuntimeMetadata` | `src/agents/state.py` | frozen dataclass | Request | Observability |
| `TraceContext` | `src/utils/logger.py` | frozen dataclass | Request (contextvar) | Infrastructure |
| `SecurityDecision` | `src/core/security/sql_security.py` | frozen dataclass | Validation | SecurityValidator |
| `ExecutionPolicy` | `src/core/database/connection.py` | frozen dataclass | Global | Config |
| `DatabaseError` | `src/core/database/connection.py` | Exception | Execution | DatabaseConnection |

### Data Flow Confirmed
```
Request → create_initial_state() → AgentState(INITIALIZED)
    → session resolution → RequestContext.session_id updated
    → history fetch → ConversationContext.history populated
    → SQL generation → SQLState.generated_sql + transition to SQL_GENERATED
    → validation → SQLState.validation_status + transition to VALIDATED
    → execution → ExecutionState + transition to EXECUTED
    → answer generation → ResponseState + transition to ANSWERED
    → state_to_api_response() → API dict
```

### Error Paths Confirmed
- `SQLValidationError`/`SQLSecurityError` → `with_validation_failed()` / `with_security_rejected()` → `FAILED`
- `QueryExecutionError` → `with_execution_error()` → `FAILED`
- Generic `Exception` → `with_error_response()` → `FAILED`

---

## 4. Problems Found

| Problem | Severity | Evidence |
|---------|----------|----------|
| Incomplete AgentState wiring in error paths | Medium | `TextToSQLAgent.ask()` lines 214-220, 242-248, 266-272 return raw dicts |
| Dual trace_id/session_id ownership | Low | `TraceContext` + `RequestContext` both carry same fields |
| Transition not enforced in all error paths | Medium | `with_error_response()` transitions but catch blocks may not use it |
| `normalized_sql`, `security_decision` fields missing | Low | `SQLState` has placeholders but not populated |
| `ExecutionState.columns`, `truncated` missing | Low | Not captured from cursor.description |
| `RuntimeMetadata.token_usage` not tracked | Low | Field exists but not populated |
| Invariant validation not called consistently | Low | `validate_invariants()` exists but not called at all transitions |

---

## 5. Target State Design

### Structure (from `docs/architecture/agent-state-design.md`)
```
AgentState
├── request: RequestContext
├── conversation: ConversationContext
├── pipeline_stage: PipelineStage
├── question_analysis: QuestionSpec?      # Future
├── schema_context: SchemaContext?        # Future
├── query_plan: QueryPlan?                # Future
├── sql: SQLState
├── execution: ExecutionState
├── result_profile: ResultProfile?        # Future
├── presentation_plan: PresentationPlan?  # Future
├── response: ResponseState
├── error: ErrorState
└── metadata: RuntimeMetadata
```

### Key Design Decisions
1. **Frozen sub-models + mutable container** — Predictable updates, safe logging
2. **Explicit stage machine** — `VALID_TRANSITIONS` enforced at every transition
3. **Single-writer ownership** — Each sub-model owned by one pipeline component
4. **Infrastructure boundaries** — No DB connections, LLM clients, MemoryManager in state
5. **TraceContext separation** — Correlation only; `AgentState` references `trace_id` at creation
6. **Memory boundary** — `ConversationContext` is immutable snapshot; no storage deps
7. **Sensitive data redaction** — Dual-mode serialization + logger sanitization
8. **Stdlib dataclass** — Zero deps, matches existing conventions

---

## 6. State Field Catalog

| Field | Type | Owner | Lifecycle | Mutable | Sensitive |
|-------|------|-------|-----------|---------|-----------|
| `request.request_id` | str | Request | Created once | No | No |
| `request.trace_id` | str | Infrastructure | Created once | No | No |
| `request.session_id` | str? | Session | Set once | No* | No |
| `request.question` | str | Request | Created once | No | **Yes** |
| `conversation.history` | tuple[dict] | Memory | Set at start | No | No |
| `pipeline_stage` | PipelineStage | Orchestrator | Per transition | No | No |
| `sql.generated_sql` | str? | Generator | SQL_GENERATED | No | **Yes** |
| `sql.sql_fingerprint` | str? | Generator | SQL_GENERATED | No | No |
| `sql.normalized_sql` | str? | Validator | VALIDATED | No | **Yes** |
| `sql.validation_status` | ValidationStatus | Validator | VALIDATED | No | No |
| `sql.security_decision` | SecurityDecision? | Security | VALIDATED | No | No |
| `execution.status` | ExecutionStatus | Executor | EXECUTED | No | No |
| `execution.rows` | tuple[dict] | Executor | EXECUTED | No | **Yes** |
| `execution.row_count` | int | Executor | EXECUTED | No | No |
| `execution.columns` | tuple[str] | Executor | EXECUTED | No | No |
| `execution.duration_ms` | float? | Executor | EXECUTED | No | No |
| `execution.truncated` | bool | Executor | EXECUTED | No | No |
| `response.answer` | str? | AnswerGen | ANSWERED | No | **Yes** |
| `response.success` | bool | Pipeline | ANSWERED/FAILED | No | No |
| `error.category` | ErrorCategory? | Pipeline | FAILED | No | No |
| `error.code` | str? | Pipeline | FAILED | No | No |
| `error.retryable` | bool | Pipeline | FAILED | No | No |
| `metadata.llm_calls` | int | Observability | Throughout | No | No |
| `metadata.stage_timings` | dict | Observability | Throughout | No | No |

---

## 7. Boundaries

### AgentState ↔ TraceContext
- **TraceContext**: Infrastructure correlation (contextvar), logging only
- **AgentState.request**: Business identity, references trace_id at creation
- **No dependency** after initialization

### AgentState ↔ MemoryManager
- **MemoryManager**: SQLite persistence, session/message CRUD
- **AgentState.conversation**: Immutable history snapshot (tuple)
- **Orchestrator** calls MemoryManager before/after state updates

### AgentState ↔ DatabaseConnection
- **DatabaseConnection**: Fresh connection/cursor per query, execution policy
- **AgentState.execution**: Immutable result snapshot (rows, count, duration)
- **No resources** in AgentState

### AgentState ↔ SecurityValidator
- **SecurityValidator**: Stateless AST policy engine → SecurityDecision
- **AgentState.sql**: Holds validation outcome (status, reason, message)
- **No validator instance** in state

### AgentState ↔ Observability
- **RuntimeMetadata**: Structured telemetry inside AgentState
- **Logger**: External, consumes `to_dict(include_sensitive=False)`
- **TraceContext**: External correlation for log events

---

## 8. Lifecycle

```
CREATED (create_initial_state)
    ↓
INITIALIZED
    ↓ (session resolved, history loaded)
QUESTION_ANALYZED (future) OR SQL_GENERATED (current)
    ↓ (LLM generates SQL)
SQL_GENERATED
    ↓ (validation passes)
VALIDATED
    ↓ (execution succeeds)
EXECUTED
    ↓ (answer generated)
ANSWERED (terminal success)

ANY STAGE
    ↓ (error)
FAILED (terminal failure)
```

### Transition Matrix
| Current | Event | Next | Allowed |
|---------|-------|------|---------|
| INITIALIZED | begin | QUESTION_ANALYZED | Yes |
| INITIALIZED | sql_generated | SQL_GENERATED | Yes (current) |
| INITIALIZED | error | FAILED | Yes |
| QUESTION_ANALYZED | schema_resolved | SCHEMA_RESOLVED | Yes |
| QUESTION_ANALYZED | error | FAILED | Yes |
| SCHEMA_RESOLVED | plan_created | PLAN_CREATED | Yes |
| SCHEMA_RESOLVED | error | FAILED | Yes |
| PLAN_CREATED | sql_generated | SQL_GENERATED | Yes |
| PLAN_CREATED | error | FAILED | Yes |
| SQL_GENERATED | validated | VALIDATED | Yes |
| SQL_GENERATED | error | FAILED | Yes |
| VALIDATED | executed | EXECUTED | Yes |
| VALIDATED | error | FAILED | Yes |
| EXECUTED | result_analyzed | RESULT_ANALYZED | Yes |
| EXECUTED | answered | ANSWERED | Yes (current) |
| EXECUTED | error | FAILED | Yes |
| RESULT_ANALYZED | answered | ANSWERED | Yes |
| RESULT_ANALYZED | error | FAILED | Yes |
| ANSWERED | — | — | No (terminal) |
| FAILED | — | — | No (terminal) |

---

## 9. Invariants

| ID | Rule | Enforcement |
|----|------|-------------|
| INV-001 | Execution success + rows > 0 → rows not empty | `validate_invariants()` |
| INV-002 | Security rejected → execution pending | `validate_invariants()` |
| INV-003 | Response success → answer present | `validate_invariants()` |
| INV-004 | Response failed → error present | `validate_invariants()` |
| INV-005 | Pipeline failed → error category present | `validate_invariants()` |
| INV-006 | Validation passed → SQL present | `validate_invariants()` |
| INV-007 | Correction attempt > 0 → SQL present | `validate_invariants()` |
| INV-008 | LLM calls ≥ 0 | Field default |
| INV-009 | Correction attempts ≤ max | `with_correction_attempt()` check |
| INV-010 | Truncated → ROW_LIMIT_EXCEEDED | `ExecutionState.with_results()` |

---

## 10. Type System Decision

**Choice**: Python `dataclass` (stdlib)

**Rationale**:
- Matches existing conventions (`SecurityDecision`, `ExecutionPolicy`, `TraceContext`)
- `frozen=True` provides immutability for sub-models
- `dataclasses.replace()` enables functional updates
- Zero external dependencies
- Pydantic reserved for API boundary (`AskRequest`, `AskResponse`)
- Mypy compatible for static checking

---

## 11. Serialization Strategy

| Method | Use Case | Sensitive Data |
|--------|----------|----------------|
| `to_dict(include_sensitive=False)` | Logging, observability | Redacted |
| `to_dict(include_sensitive=True)` | Debugging, future persistence | Plain |
| `state_to_api_response()` | API/CLI/Streamlit response | Plain (API authorized) |
| `safe_repr()` / `__repr__` | Debug logging | Redacted (minimal) |

**Persistence**: Out of scope for Sprint 2. `to_dict(True)` provides full state when needed.

---

## 12. Sensitive Data Strategy

| Field | Store | Log | Safe Serialize | Full Serialize | API Expose |
|-------|-------|-----|----------------|----------------|------------|
| question | Yes | Redacted | Redacted | Plain | Yes |
| generated_sql | Yes | Redacted | Redacted | Plain | Yes |
| normalized_sql | Yes | Redacted | Redacted | Plain | No |
| rows | Yes | Redacted | Redacted | Plain | Yes |
| answer | Yes | Redacted | Redacted | Plain | Yes |
| trace_id | Yes | Plain | Plain | Plain | Header only |
| session_id | Yes | Plain | Plain | Plain | Yes |
| error.message | Yes | Plain | Plain | Plain | Yes (sanitized) |

**Implementation**: `to_dict(include_sensitive=False)` + `logger.sanitize()` both apply redaction.

---

## 13. Proposed File Structure (for Prompt 1B)

```
src/agents/
├── state.py          # AgentState + sub-models (EXISTS - enhance)
├── text_to_sql_agent.py  # Wire AgentState fully (MODIFY)
├── __init__.py

docs/
├── audit/
│   └── current-agent-state.md      # UPDATED (this audit)
├── architecture/
│   └── agent-state-design.md       # CREATED (target design)
├── contracts/
│   └── agent-state.md              # CREATED (formal contract)
└── sprints/
    └── sprint-2-prompt-1a-report.md  # CREATED (this report)
```

**No new runtime files** — only documentation and enhancements to existing `state.py`.

---

## 14. Migration Considerations

1. **Backward Compatibility**: `state_to_api_response()` must produce identical dict to current `TextToSQLAgent.ask()` return value
2. **CLI/Streamlit**: Both consume API response dict — no changes needed
3. **Evaluation Adapter**: `EvaluationAgentAdapter.normalize_response()` expects same dict format
4. **Existing Tests**: All 101 Sprint 1 tests must pass without modification
5. **Incremental**: Wire `AgentState` internally; keep external interface identical

---

## 15. Open Questions

| Question | Recommendation |
|----------|----------------|
| Token accounting in `RuntimeMetadata`? | Add `token_usage: dict = {}` now; populate when LLMRouter (ADR-011) exists |
| Full `SecurityDecision` in `SQLState`? | Store as `Any` for now; type when security types stabilized |
| Remove pipeline shortcuts (INITIALIZED→SQL_GENERATED)? | Keep for current architecture; remove when full pipeline implemented |
| `ErrorState.trace_reference` = `trace_id` or separate? | Use `RequestContext.trace_id`; add field when observability needs it |

---

## 16. Recommendation for Prompt 1B

### Implement (In Scope)
1. **Complete AgentState wiring** in `TextToSQLAgent.ask()`:
   - Replace all raw dict returns with `state_to_api_response(state)`
   - Ensure all error paths use `with_validation_failed()`, `with_security_rejected()`, `with_execution_error()`, `with_error_response()`
   - Call `state.validate_invariants()` at each transition (log warnings)

2. **Enhance `src/agents/state.py`**:
   - Add `normalized_sql`, `security_decision`, `columns`, `truncated` to `SQLState`/`ExecutionState`
   - Populate `ExecutionState.columns` from cursor.description
   - Add `token_usage` to `RuntimeMetadata`
   - Type future placeholders (`question_analysis`, `schema_context`, etc.) when components exist

3. **Add Unit Tests** (new file: `tests/test_agents/test_state.py`):
   - `test_create_initial_state`
   - `test_transition_validation`
   - `test_sql_generation_flow`
   - `test_execution_flow`
   - `test_error_flows`
   - `test_invariants`
   - `test_serialization_safe` / `test_serialization_full`
   - `test_request_isolation`
   - `test_api_response_compat`

4. **Verify**: All 101 existing tests pass + new tests pass

### Do Not Implement (Out of Scope)
- New orchestrator / pipeline components
- QuestionAnalyzer, SchemaCatalog, QueryPlanner, etc.
- Memory redesign (ADR-009)
- AgentState persistence
- API contract changes
- CLI/Streamlit behavior changes

---

## Files Created/Modified

### Created
- `docs/architecture/agent-state-design.md` — Target design document
- `docs/contracts/agent-state.md` — Formal contract
- `docs/sprints/sprint-2-prompt-1a-report.md` — This report

### Modified
- `docs/audit/current-agent-state.md` — Updated with complete audit findings

### Code Changes
**None** — This prompt is audit/design only. No runtime code modified.

---

## Definition of Done Verification

| Criterion | Status |
|-----------|--------|
| Current Agent State Flow fully audited | ✅ |
| Existing state/data objects identified | ✅ |
| State ownership analyzed | ✅ |
| Business vs infrastructure state separated | ✅ |
| Request context boundary defined | ✅ |
| TraceContext boundary defined | ✅ |
| Memory boundary defined | ✅ |
| SQL State designed | ✅ |
| Execution State designed | ✅ |
| Response State designed | ✅ |
| Error State designed | ✅ |
| Runtime metadata designed | ✅ |
| Future component compatibility considered | ✅ |
| AgentState schema designed | ✅ |
| Field definitions documented | ✅ |
| Type-system decision documented | ✅ |
| Mutability strategy documented | ✅ |
| Request isolation strategy documented | ✅ |
| Serialization strategy documented | ✅ |
| Sensitive-data strategy documented | ✅ |
| Safe representation strategy documented | ✅ |
| State invariants documented | ✅ |
| Lifecycle documented | ✅ |
| Transition matrix documented | ✅ |
| Dependency boundaries documented | ✅ |
| Target file placement documented | ✅ |
| No runtime/code implementation performed | ✅ |

**Definition of Done: PASS**

---

## Final Verification

- [x] No runtime code changed
- [x] No Agent behavior changed
- [x] No Prompt changed
- [x] No API changed
- [x] No Database behavior changed
- [x] Documentation is internally consistent
- [x] Design is consistent with Sprint 0/1 architecture
- [x] No unresolved contradiction between docs and code is hidden

---

**Next Step**: Prompt 1B — Implement AgentState wiring and tests per recommendations above.