# Sprint 2 — Prompt 2B Report: Core Interface Implementation & Orchestrator Plumbing

**Date**: 2026-08-31
**Sprint**: 2
**Prompt**: 2B
**Status**: Complete
**Authoritative sources**:
- [`docs/contracts/interface-design.md`](../contracts/interface-design.md) (Prompt 2A)
- [`docs/contracts/component-contracts.md`](../contracts/component-contracts.md) (Prompt 2A)
- [`docs/architecture/dependency-rules.md`](../architecture/dependency-rules.md) (Prompt 2A)
- [`docs/architecture/agent-state-design.md`](../architecture/agent-state-design.md) (Prompt 1A)
- [`docs/sprints/sprint-2-prompt-2a-report.md`](./sprint-2-prompt-2a-report.md)
- [`docs/adr/ADR-012..015.md`](../adr/)

---

## 1. Purpose

Prompt 2B turns the design outputs of Prompt 2A into **executable
plumbing**: typed value objects, runtime-checkable Protocols, typed
error contracts, the `Orchestrator` class, and the architecture tests
that mechanically enforce the dependency rules.

**This prompt does NOT implement component intelligence.** Future
components (`QuestionAnalyzer`, `SchemaRetriever`, `QueryPlanner`,
`SQLGenerator`, `ResultAnalyzer`, `PresentationPlanner`,
`AnswerGenerator`, `AnswerValidator`, `LLMRouter`) are stubbed with
Protocol definitions. Their concrete implementations land in later
sprints.

The deliverable is the **scaffolding** that future components plug into
without needing to re-litigate interface boundaries.

---

## 2. Goals (per Prompt 2B specification)

| # | Goal | Outcome |
|---|------|---------|
| G1 | Define the single source of truth for value objects, Protocols, errors | `src/agents/contracts.py` |
| G2 | Make every component replaceable behind a `runtime_checkable` Protocol | 17 Protocols |
| G3 | Introduce a typed error hierarchy with stable codes & stage tags | `TypedComponentError` + 11 domain errors |
| G4 | Make the Orchestrator the **sole** writer of `AgentState` | `src/agents/orchestrator.py` |
| G5 | Wrap existing components behind adapters | `src/agents/adapters.py` |
| G6 | Preserve public API & CLI behavior | `TextToSQLAgent` becomes a façade |
| G7 | Add architecture tests that enforce dependency rules | `tests/test_architecture/` (32 tests) |
| G8 | Document what was built | This report + doc updates |

---

## 3. Deliverables

### 3.1 New source modules

| Path | Lines | Purpose |
|------|-------|---------|
| `src/agents/contracts.py` | ~900 | Typed value objects, Protocol interfaces, typed error hierarchy, `ErrorStageTag` enum |
| `src/agents/orchestrator.py` | ~360 | `Orchestrator` class, `OrchestratorDeps` DI container |
| `src/agents/adapters.py` | ~210 | `SecurityValidatorAdapter`, `QueryExecutorAdapter` |

### 3.2 Modified source modules

| Path | Change |
|------|--------|
| `src/agents/state.py` | Added 6 state-applier methods; re-typed `SQLState.security_decision` as `Optional[SecurityDecision]`; extended `ErrorCategory` with `ANSWER_VALIDATION`, `MEMORY`, `LLM`; updated `to_dict()` |
| `src/agents/text_to_sql_agent.py` | Rewritten as a thin façade that delegates to `Orchestrator` |
| `src/agents/__init__.py` | Added exports for `Orchestrator`, `OrchestratorDeps`, `SecurityValidatorAdapter`, `QueryExecutorAdapter` |

### 3.3 New test modules

| Path | Tests | Purpose |
|------|-------|---------|
| `tests/test_agents/test_contracts.py` | 65 | Value object + error type tests |
| `tests/test_agents/test_orchestrator.py` | 19 | Orchestrator behavior, dependency injection, state transitions, single-writer |
| `tests/test_agents/test_state_appliers.py` | 8 | New state-applier methods |
| `tests/test_architecture/test_dependencies.py` | 19 | Static import-graph dependency rules |
| `tests/test_architecture/test_single_writer.py` | 5 | State mutation rule enforcement |
| `tests/test_architecture/test_backward_compatibility.py` | 8 | API / CLI / evaluation adapter / session round-trip |

**Total: 333 passing tests** (209 baseline + 124 new in Prompt 2B).

### 3.4 Documentation

| Path | Change |
|------|--------|
| `docs/architecture/dependency-rules.md` | §6, §10 updated to reflect implemented tests |
| `docs/architecture/agent-state-design.md` | State-applier table extended with 6 new methods |
| `docs/sprints/sprint-2-prompt-2b-report.md` | This document |

---

## 4. Architecture After Prompt 2B

```
                  ┌──────────────────────┐
                  │   Presentation       │
                  │  (FastAPI, CLI, UI)  │
                  └──────────┬───────────┘
                             │  ask(question)
                             ▼
                  ┌──────────────────────┐
                  │  TextToSQLAgent      │   (façade — backward compat)
                  │  ┌────────────────┐  │
                  │  │ Orchestrator   │  │   ← sole writer of AgentState
                  │  └────────────────┘  │
                  └──────────┬───────────┘
                             │
        ┌────────┬────────┬──┴──┬────────┬────────┬────────┐
        ▼        ▼        ▼     ▼        ▼        ▼        ▼
   Question   Schema   Query  SQL    Result  Presentation  Answer
   Analyzer   Retriever Planner Gener. Analyzer  Planner   Generator
        │        │        │     │        │        │        │
        └────────┴────────┴─────┴────────┴────────┴────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │   Validators         │   (SQL, Security, Semantic,
                  │   (deterministic)    │    Complexity)
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  Infrastructure      │   QueryExecutor, MemoryManager,
                  │  Adapters            │   LLMRouter, SchemaCatalog
                  └──────────────────────┘
```

- Components receive **typed value objects** (frozen dataclasses).
- Components return **typed results**.
- The **Orchestrator** is the only writer of `AgentState`.
- Components do not import `AgentState`. (Enforced.)
- Components do not import provider SDKs. (Enforced.)
- The **API layer** depends only on the public façade. (Enforced.)

---

## 5. Key Design Decisions

### 5.1 Contract Module Placement

**Decision**: Place value objects, Protocols, and typed errors in
**one** module: `src/agents/contracts.py`.

**Rationale**:
- Reduces import churn across the codebase — every component
  imports from one place.
- Avoids circular imports between value objects and Protocols.
- Keeps the contracts discoverable: a new contributor can read one
  file and understand the type surface.

**Trade-off**: The file is large (~900 lines). We accept this because
the alternative (splitting into many small modules) creates more
import noise and makes circular import avoidance harder. The contracts
are conceptually one unit: "what flows between components".

### 5.2 Protocol Style: `runtime_checkable`

**Decision**: Use `@runtime_checkable` Protocols.

**Rationale**:
- Allows `isinstance(obj, Protocol)` checks in the architecture
  tests and at the Orchestrator boundary.
- Components can be duck-typed; the runtime check is optional and
  not used in hot paths.
- Forward-compatible: a future component does not need to inherit
  from a base class.

**Trade-off**: `runtime_checkable` Protocols only verify attribute
presence, not signatures. Full type safety is provided by mypy in
CI (see Future Work).

### 5.3 Typed Error Hierarchy

**Decision**: Introduce a separate `TypedComponentError` base class
that carries `code`, `stage`, `category`, `retryable`, `cause`. Each
domain error (`QuestionAnalysisError`, `SchemaError`, etc.) inherits
from it.

**Rationale**:
- Existing exception classes (`SQLValidationError`, `SQLSecurityError`,
  `QueryExecutionError`, `DatabaseError`, `LLMError`) remain
  **unchanged and catchable** to preserve backward compatibility
  with any code that imports them.
- New typed errors are **additive only** — they are catchable
  *additionally*, not as replacements.
- `ErrorStageTag` is a separate enum from `PipelineStage` because
  the stage tag describes *where the error was raised*, not *what
  stage the pipeline is currently in*. They happen to overlap, but
  the conceptual separation is clean.

### 5.4 `ErrorCategory` Extension

**Decision**: Add `ANSWER_VALIDATION`, `MEMORY`, `LLM` to the existing
`ErrorCategory` enum.

**Rationale**: These correspond to the new `AnswerValidator`,
`MemoryManager`, and `LLMRouter` components. The enum is extended
additively (no existing values removed).

### 5.5 Adapters for Existing Components

**Decision**: Wrap `SQLSecurityValidator` and `DatabaseConnection` in
thin adapters (`SecurityValidatorAdapter`, `QueryExecutorAdapter`).

**Rationale**:
- The existing classes predate the target architecture and don't
  satisfy the new contracts exactly. Forcing them into the new
  shape would create a large refactor PR. Adapters isolate the
  legacy interface from the new one.
- The adapters are < 50 lines each, easy to test, and easy to
  remove when the legacy classes are deleted in a future sprint.

**Trade-off**: One indirection layer. Acceptable because it is the
*only* way to avoid rewriting `DatabaseConnection` and
`SQLSecurityValidator` in Prompt 2B.

### 5.6 `OrchestratorDeps` DI Container

**Decision**: The `Orchestrator` receives a `OrchestratorDeps`
dataclass with one slot per required dependency
(`sql_extractor`, `query_executor`, `llm_client`, `prompts`,
`memory_manager`, `security_validator`).

**Rationale**:
- The `Orchestrator` does not own infrastructure. It receives
  Protocol-typed dependencies.
- Tests can construct a `OrchestratorDeps` with fakes; no
  monkey-patching required.
- The DI container is a frozen dataclass — it is itself an
  immutable value object.

**Future**: When `LLMRouter` and `SchemaRetriever` exist, they
will be added to `OrchestratorDeps` (additive change).

### 5.7 `TextToSQLAgent` as Façade

**Decision**: `TextToSQLAgent` is rewritten as a thin façade that:
1. Constructs `OrchestratorDeps` from its existing attributes
   (`extractor`, `db`, `llm`, `memory`, `prompts`).
2. Constructs an `Orchestrator` with those deps.
3. Creates an `AgentState` via `create_initial_state(...)`.
4. Calls `Orchestrator.run(state)`.
5. Converts the final state to a public dict via
   `state_to_api_response(state)`.

**Rationale**:
- The constructor signature `(self)` is **unchanged** — `run.py`
  still works without modification.
- The public dict format is **unchanged** — all consumers
  (FastAPI, CLI, Streamlit, evaluation) still work.
- All Sprint 1 / Prompt 1A/1B/1C tests **still pass** without
  modification.

### 5.8 State Applier Methods

**Decision**: Add six new `with_*` state-applier methods to
`AgentState`: `with_question_analysis`, `with_schema_context`,
`with_query_plan`, `with_result_profile`, `with_presentation_plan`,
`with_validated_answer`.

**Rationale**:
- Each applier stores a typed value object in the corresponding
  `AgentState` field and transitions `pipeline_stage` (where
  appropriate) via `transition_to(...)`.
- Future components return typed results; the Orchestrator calls
  the applier. The applier is the **only** way to write
  `question_analysis`, `schema_context`, etc.
- `with_presentation_plan` does NOT transition stage because
  presentation is a side-effect of `RESULT_ANALYZED` →
  `ANSWERED`. It updates the field only.
- `with_validated_answer` IS a transition applier (it moves
  to `ANSWERED`).

### 5.9 Transition Table Update

The valid transitions remain the same as Prompt 1A/1B. The new
appliers hook into the existing transition map:

- `INITIALIZED → QUESTION_ANALYZED` (via `with_question_analysis`)
- `QUESTION_ANALYZED → SCHEMA_RESOLVED` (via `with_schema_context`)
- `SCHEMA_RESOLVED → PLAN_CREATED` (via `with_query_plan`)
- `RESULT_ANALYZED → ANSWERED` (via `with_validated_answer`)

The transition map requires the Orchestrator to walk the stages
in order, which prevents shortcuts. (Future components can only
transition to the next legal stage.)

### 5.10 `SQLState.security_decision` Re-typed

**Decision**: Change `SQLState.security_decision` from `Any` to
`Optional[SecurityDecision]` (imported from
`src/core/security/sql_security.py`).

**Rationale**:
- The existing `Any` was a placeholder (per Open Question #2 in
  `agent-state-design.md`).
- Now that `SecurityValidatorAdapter` is the canonical writer of
  `SecurityDecision`, the type can be tightened.
- The new type makes the contract self-documenting: anyone reading
  `state.sql.security_decision` knows exactly what to expect.

**Backward compatibility**: `SecurityDecision` is already a frozen
dataclass. Existing code that accesses
`state.sql.security_decision.is_security_rejected` continues to
work because that property exists on `SecurityDecision`.

### 5.11 Where the Orchestrator Lives

**Decision**: `Orchestrator` lives in `src/agents/orchestrator.py`
alongside `text_to_sql_agent.py` and `adapters.py`.

**Rationale**: The Orchestrator is an internal application
component, not a public façade. Keeping it in `src/agents/`
makes the import path short and discoverable. The façade
(`TextToSQLAgent`) and the Orchestrator are intentionally
**separate** classes in the same package.

### 5.12 Façade Pattern, Not Inheritance

**Decision**: `TextToSQLAgent` does **not** inherit from
`Orchestrator`. It **uses** an `Orchestrator`.

**Rationale**:
- Inheritance would couple the public façade to the internal
  orchestration logic, making future changes harder.
- Composition (`TextToSQLAgent` holds an `Orchestrator` and
  delegates) keeps the façade small and the Orchestrator
  independently testable.
- The façade's job is **compatibility**, not orchestration.

---

## 6. Step-by-Step Results

### Step 2B.1 — Gap Analysis

| Existing | Target | Action |
|----------|--------|--------|
| `TextToSQLAgent` (god object) | `Orchestrator` + components | Split into Orchestrator + façade |
| `SQLExtractor` | `SQLGenerator` + `SQLRepairer` | Wrap as `SQLExtractorAdapter` (future) |
| `SQLSecurityValidator` | `SecurityValidator` Protocol | Wrap as `SecurityValidatorAdapter` ✅ |
| `DatabaseConnection` | `QueryExecutor` Protocol | Wrap as `QueryExecutorAdapter` ✅ |
| `MemoryManager` | `MemoryManager` Protocol | No change (already conforms) |
| `LLMClient` | `LLMRouter` Protocol | Future; current `LLMClient` retained |
| `AgentState` | `AgentState` (typed) | Re-type `security_decision`, add appliers ✅ |

### Step 2B.2 — Value Objects

Implemented in `src/agents/contracts.py`:

- `TableRef`, `ColumnRef`, `SchemaSnapshot`, `SchemaContext`
- `Filter`, `Metric`, `TimeConstraint`, `Entity`, `QuestionSpec`,
  `AmbiguityReport`
- `PlanFilter`, `PlanMetric`, `OrderingSpec`, `QueryPlan`
- `SQLCandidate`, `SQLFingerprint`
- `SQLParseResult`, `ValidationReport`, `SecurityDecision` (re-export
  from existing module), `SemanticReport`, `ComplexityReport`
- `Evidence`, `ResultProfile`, `ShapeDescriptor`
- `LocalePolicy`, `PresentationPlan`, `ViewKind`
- `AnswerDraft`, `ValidatedAnswer`

All are `@dataclass(frozen=True)`.

### Step 2B.3 — Protocol Interfaces

17 `@runtime_checkable` Protocols in `src/agents/contracts.py`:

- `QuestionAnalyzerProtocol`
- `SchemaCatalogProtocol`
- `SchemaRetrieverProtocol`
- `QueryPlannerProtocol`
- `SQLGeneratorProtocol`
- `SQLValidatorProtocol`
- `SecurityValidatorProtocol`
- `SemanticValidatorProtocol`
- `ComplexityValidatorProtocol`
- `SQLRepairerProtocol`
- `QueryExecutorProtocol`
- `ResultAnalyzerProtocol`
- `PresentationPlannerProtocol`
- `AnswerGeneratorProtocol`
- `AnswerValidatorProtocol`
- `MemoryManagerProtocol`
- `LLMRouterProtocol`

### Step 2B.4 — Error Hierarchy

- `TypedComponentError` (base)
- `QuestionAnalysisError`
- `SchemaError`
- `PlanningError`
- `SQLGenerationError`
- `RepairError`
- `SQLValidationError` (component-side, not the existing
  infrastructure-side `SQLValidationError` in
  `src/utils/exceptions.py`)
- `SecurityError`
- `SemanticValidationError`
- `ComplexityError`
- `ResultAnalysisError`
- `PresentationError`
- `AnswerGenerationError`
- `AnswerValidationError`
- `MemoryError`
- `LLMRouterError`
- `ErrorStageTag` (enum: `INITIALIZED`, `QUESTION_ANALYZED`,
  `SCHEMA_RESOLVED`, `PLAN_CREATED`, `SQL_GENERATED`, `VALIDATED`,
  `EXECUTED`, `RESULT_ANALYZED`, `PRESENTED`, `ANSWERED`, `FAILED`)

### Step 2B.5 — `AgentState` Extensions

Six new state-applier methods (see §5.8) plus
`SQLState.security_decision` re-typed.

### Step 2B.6 — Adapters

- `SecurityValidatorAdapter` wraps `SQLSecurityValidator`,
  returns a `SecurityDecision`.
- `QueryExecutorAdapter` wraps `DatabaseConnection`, returns a
  typed `QueryResult`-equivalent dict that maps to
  `ExecutionState`.

### Step 2B.7 — Orchestrator

- `OrchestratorDeps`: frozen dataclass DI container.
- `Orchestrator.run(state)`: single entry point that walks
  the happy path (current code) and the failure path
  (security reject, exception, retry boundary).
- All state mutations go through `state.with_*` methods.

### Step 2B.8 — Façade Migration

`TextToSQLAgent.ask()` now:
1. Creates `OrchestratorDeps` from `self.*` attributes.
2. Creates `Orchestrator(deps)`.
3. Calls `create_initial_state(...)`.
4. Calls `orchestrator.run(state)`.
5. Returns `state_to_api_response(state)`.

Public dict format: **unchanged**.

### Step 2B.9 — Architecture Tests

32 tests across 3 files:

- `test_dependencies.py` (19):
  - Contracts module does not import AgentState
  - Orchestrator does not import provider SDKs
  - Adapters do not import AgentState
  - API module does not import Orchestrator
  - (Other component-level rules via parametrize)
- `test_single_writer.py` (5):
  - Future components do not import AgentState
  - Only Orchestrator calls `state.with_*`
  - State module exposes all required appliers
  - SecurityDecision typing is correct
- `test_backward_compatibility.py` (8):
  - AskRequest/Response schemas unchanged
  - `state_to_api_response` success/failure shape unchanged
  - Evaluation adapter normalizes ask response
  - Session round-trip works through Orchestrator
  - CLI constructor signature unchanged

### Step 2B.10 — Component Tests

- 65 value object + error tests (`test_contracts.py`).
- 19 orchestrator behavior tests (`test_orchestrator.py`).
- 8 state-applier tests (`test_state_appliers.py`).

### Step 2B.11 — Documentation

- `dependency-rules.md` updated.
- `agent-state-design.md` updated.
- This report (`sprint-2-prompt-2b-report.md`).

---

## 7. Test Results

```
333 passed in 5.49s
```

Breakdown:

| Test file | Count |
|-----------|------:|
| Sprint 1 / Prompt 1A/1B/1C baseline | 209 |
| `tests/test_agents/test_contracts.py` (new) | 65 |
| `tests/test_agents/test_orchestrator.py` (new) | 19 |
| `tests/test_agents/test_state_appliers.py` (new) | 8 |
| `tests/test_architecture/test_dependencies.py` (new) | 19 |
| `tests/test_architecture/test_single_writer.py` (new) | 5 |
| `tests/test_architecture/test_backward_compatibility.py` (new) | 8 |
| **Total** | **333** |

`compileall` clean: `python -m compileall -q src run.py run_api.py run_ui.py evaluation`.

---

## 8. Backward Compatibility

| Surface | Status | Verified by |
|---------|--------|-------------|
| `POST /api/v1/ask` request/response | unchanged | `test_ask_request_schema`, `test_ask_response_schema` |
| `state_to_api_response` format | unchanged | `test_success_response_shape`, `test_failure_response_shape` |
| `TextToSQLAgent` constructor | unchanged | `test_constructor_signature_unchanged` |
| `TextToSQLAgent.ask` return dict | unchanged | `test_ask_returns_dict_with_success_key` |
| `EvaluationAgentAdapter.normalize_response` | unchanged | `test_evaluation_adapter_normalizes_ask_response` |
| Session round-trip | unchanged | `test_session_round_trip_through_orchestrator` |
| CLI `run.py` | unchanged | (manual smoke; constructor signature test) |
| Streamlit `src/web/app.py` | unchanged | (uses same dict format) |

---

## 9. Single-Writer Rule

Enforced by:
- `tests/test_architecture/test_single_writer.py::test_future_component_does_not_import_agent_state`
- `tests/test_architecture/test_single_writer.py::test_orchestrator_is_the_only_state_writer_in_new_modules`
- `tests/test_architecture/test_dependencies.py::TestContractsModuleBoundaries::test_does_not_import_agent_state`
- `tests/test_architecture/test_dependencies.py::TestAdaptersModuleBoundaries::test_does_not_import_agent_state`

Verified at the AST level — no future component module imports
`src.agents.state`, and no future component module calls
`state.with_*`.

---

## 10. Open Questions Deferred to Future Sprints

| # | Question | Deferred to |
|---|----------|-------------|
| Q1 | Should `SQLGenerator` and `SQLRepairer` be the same Protocol or two? | Sprint 4 (SQL pipeline) |
| Q2 | Should `OrchestratorDeps` grow an `llm_router` slot now or when router is built? | Sprint 6 (LLM) |
| Q3 | Should the Orchestrator be async or stay sync? | Sprint 6 (LLM) |
| Q4 | Should the `PresentationPlan` applier be a transition applier? | Sprint 8 (Answer) |
| Q5 | Should the `MemoryManager` Protocol live in `src/memory/` instead of `src/agents/contracts.py`? | Sprint 11 (Memory) |

---

## 11. Migration Plan for Sprint 3+ (Future)

1. **Sprint 3 (Schemas)**: Implement `SchemaCatalog` and
   `SchemaRetriever` against the new Protocols. Add to
   `OrchestratorDeps` (additive). Replace
   `TextToSQLAgent._static_schema_fallback` with the new
   retriever.

2. **Sprint 4 (SQL pipeline)**: Implement `SQLGenerator` and
   `SQLRepairer` against the new Protocols. Replace
   `SQLExtractor` (legacy) with the new generator. Add repair
   loop to Orchestrator.

3. **Sprint 5 (Validators)**: Implement `SemanticValidator` and
   `ComplexityValidator` against the new Protocols. Add to
   Orchestrator.

4. **Sprint 6 (LLM)**: Implement `LLMRouter`. Add to
   `OrchestratorDeps`. Replace `LLMClient` calls with
   `LLMRouter.generate(...)`.

5. **Sprint 7 (Results)**: Implement `ResultAnalyzer` against
   the new Protocol. Wire to Orchestrator.

6. **Sprint 8 (Answer)**: Implement `PresentationPlanner`,
   `AnswerGenerator`, `AnswerValidator` against the new
   Protocols. Wire to Orchestrator.

7. **Sprint 11 (Memory)**: Replace `MemoryManager` with
   typed-turn-model implementation. Adapters can be removed
   once the underlying classes conform to the Protocols.

---

## 12. Risks & Trade-offs

| Risk | Mitigation |
|------|------------|
| `runtime_checkable` Protocols only check attribute presence, not signatures. | mypy in CI for new components. |
| Large `contracts.py` (~900 lines). | Documented as intentional; future splits possible if file becomes a maintenance burden. |
| Two parallel `SQLSecurityDecision` types (one in `src/core/security/sql_security.py`, one as re-export in `contracts.py`). | The re-export is identical; no duplication of code, only of import path. |
| `OrchestratorDeps` grows as new components are added. | Acceptable; the DI container is additive. |
| `TextToSQLAgent` constructor still creates a `DatabaseConnection` (legacy infra ownership). | Out of scope for Prompt 2B; resolved when `OrchestratorDeps` is constructed by the application bootstrap (Sprint 3+). |

---

## 13. Verification Checklist

- [x] All 333 tests pass.
- [x] `compileall` clean.
- [x] `git diff --check` clean (only line-ending warnings on
      Windows).
- [x] Single-writer rule enforced.
- [x] API / CLI / evaluation adapter backward compatible.
- [x] Typed value objects in `contracts.py`.
- [x] Protocol interfaces in `contracts.py`.
- [x] Typed error hierarchy in `contracts.py`.
- [x] `Orchestrator` is sole writer of `AgentState`.
- [x] `TextToSQLAgent` is a façade.
- [x] Adapters for legacy components.
- [x] Architecture tests in `tests/test_architecture/`.
- [x] No new dependencies in `requirements.txt` (used stdlib +
      existing third-party packages only).
- [x] Documentation updated.

---

## 14. Summary

Sprint 2 Prompt 2B delivered the **scaffolding** for the target
architecture: contracts, protocols, errors, the orchestrator, the
adapters, and the architecture tests that mechanically enforce the
dependency rules. **No component intelligence was added** — that
work is intentionally deferred to Sprints 3–11.

The codebase now has a stable interface surface that future sprints
can implement against without renegotiating boundaries. The single-
writer rule, the no-`AgentState`-import rule, and the no-provider-SDK
rule are **mechanically enforced by tests**, not just by convention.

The public API, the CLI, the Streamlit UI, and the evaluation
adapter are all **unchanged**. All 209 baseline tests still pass.
