# Dependency Rules

**Sprint**: 2 — Prompt 2A (design) / Prompt 2B (implementation of architectural tests)
**Status**: Authoritative; **enforced by static import-graph tests in `tests/test_architecture/`**
**Companion documents**:
- [`docs/contracts/interface-design.md`](../contracts/interface-design.md)
- [`docs/contracts/component-contracts.md`](../contracts/component-contracts.md)
- [`docs/sprints/sprint-2-prompt-2b-report.md`](../sprints/sprint-2-prompt-2b-report.md)
- [`docs/architecture/package-architecture.md`](package-architecture.md) — Sprint 2 Prompt 3A target package design (ADR-016)
- [`docs/architecture/developer-package-guide.md`](developer-package-guide.md) — practical developer summary of these rules and their enforcing tests

---

## 1. Purpose

This document states the **allowed** and **forbidden** dependencies between
target components. It is the basis for future architecture tests
(Sprint 2 Prompt 2B and beyond) that prevent components from acquiring
hidden responsibilities or coupling.

A **dependency** in this document means a Python `import` or a runtime
call from one component to another. The contract document
`component-contracts.md` lists allowed and forbidden dependencies per
component. This document summarizes them as a single architecture-wide
matrix and explains the rationale.

---

## 2. Layering Rules

The target architecture defines the following layering:

```
Presentation  ──►  Application  ──►  Domain  ──►  Validator  ──►  Infrastructure  ──►  External
```

Rules:

1. **A higher layer may depend on a lower layer.**
2. **A lower layer MUST NOT depend on a higher layer.**
3. **Two components in the same layer MAY depend on each other only when
   the dependency is documented and minimal.**
4. **No component may skip a layer in the dependency graph.** A domain
   component cannot import an external provider SDK directly; it must
   import an infrastructure adapter (`LLMRouter`).
5. **No component in any layer may import `AgentState`.** State is the
   orchestrator's concern. Components receive typed value objects.

---

## 3. Component-Level Allowed Dependencies

The following dependencies are **allowed**. Anything not listed here is
forbidden unless accompanied by a new ADR.

### 3.1 QuestionAnalyzer

- `LLMRouter`
- `ConversationContext` (typed value object from AgentState, passed in by
  parameter)
- `log_event`, `TraceContext` (cross-cutting observability)

### 3.2 SchemaCatalog

- `pymssql` driver (read-only admin connection, used only at refresh time) — **refined in Sprint 3.1B (ADR-017)**: the catalog itself imports **no** driver; structural discovery crosses through the metadata adapter `src/core/database/metadata.py` (the second approved, lazy driver import site), which returns driver-free typed metadata. The admin connection remains separate from the per-request `QueryExecutor` connection.
- File system cache (`os.path`, JSON): annotations `data/schema/annotations.json` + content-addressed snapshot cache `data/schema/schema_cache.json` (legacy `.schema_cache.json` untouched)
- `log_event`, `TraceContext`
- `src.agents.contracts` (canonical schema value objects)

Implemented as `src/core/schema/catalog.py` (Sprint 3.1B); enforced by
`tests/test_architecture/test_schema_boundary.py` (no driver/LLM/SDK/state/API/memory imports in `src/core/schema/**`), the whole-tree pymssql-site scan in `test_boundary_hardening.py`, and fresh-subprocess footprint probes.

### 3.3 SchemaRetriever

- `SchemaSnapshot` (typed value object)
- `QuestionSpec` (typed value object, passed in by parameter)

Implemented as `src/core/schema/retriever.py` (Sprint 3.1C): pure,
deterministic, LLM/DB/state/memory-free selection; consumed only by the
Orchestrator through the existing `OrchestratorDeps` seam (dormant until
a QuestionAnalyzer supplies a QuestionSpec). Enforced by
`tests/test_architecture/test_schema_boundary.py`.

**Phase 3.2**: the retriever additionally emits the contract-named
`schema_retrieval_completed` event via the shared `log_event` surface
(§3.18/§7 — observability is not a counted dependency) and implements
hard context bounds, deterministic budgeting and query-type-aware
evidence routing (schema-intelligence.md §20).

### 3.3a SchemaContextRenderer (Phase 3.2)

- `SchemaContext` (typed value object, passed in by parameter)

Implemented as `src/core/schema/renderer.py`: pure deterministic
*formatting* of an already-bounded request-level context for LLM-facing
consumers — DB-free, LLM-free, retrieval-free, catalog-free,
AgentState-free. It imports only `src.agents.contracts`. It is dormant
until the separately-authorized prompt-migration stage consumes it.
Enforced by `tests/test_architecture/test_schema_boundary.py`
(renderer isolation + fresh-subprocess footprint) and
`tests/test_schema/test_context_renderer.py`.

### 3.4 QueryPlanner

- `LLMRouter`
- `QuestionSpec`, `SchemaContext`

### 3.5 SQLGenerator

- `LLMRouter`
- `QueryPlan`, `SchemaContext`
- `sql_fingerprint` (utility for fingerprinting)

### 3.6 SQLValidator

- `sqlglot` parser

### 3.7 SecurityValidator

- `sqlglot` parser
- `SecurityPolicy` (typed value object)

### 3.8 SemanticValidator

- Parsed AST (typed value object)
- `QueryPlan`, `SchemaSnapshot`

### 3.9 ComplexityValidator

- Parsed AST
- `ComplexityBudget` (typed value object)

### 3.10 QueryExecutor

- `pymssql` driver
- `ExecutionPolicy` (typed value object)
- Integration with `SecurityValidator` BEFORE `execute` is called (this
  is a documented integration, not a runtime import; the current
  `DatabaseConnection.execute_query` performs the security check inline,
  which is preserved)

### 3.11 ResultAnalyzer

- `QueryResult`, `QueryPlan`

### 3.12 PresentationPlanner

- `QuestionSpec`, `ResultProfile`, `LocalePolicy`, `SchemaSnapshot.labels`

### 3.13 AnswerGenerator

- `LLMRouter`
- `QueryResult`, `ResultProfile`, `PresentationPlan`, `QuestionSpec`

### 3.14 AnswerValidator

- `AnswerDraft`, `ResultProfile`, `PresentationPlan`

### 3.15 SQLRepairer

- `LLMRouter`
- `QueryPlan`, validator reports (`ValidationReport`, `SecurityDecision`,
  `SemanticReport`, `ComplexityReport`)

### 3.16 MemoryManager

- `sqlite3` stdlib
- `os` for path handling

### 3.17 LLMRouter

- Provider SDKs: `OpenAIClient`, `GeminiClient`, `GroqClient`,
  `OllamaClient`
- `requests` (where already used)
- `log_event`, `TraceContext`
- `LLMPolicy`, `LLMBudget` (typed value objects)

**Sprint 3B status**: implemented as `src/core/llm/router.py`. The
router imports the value objects from the driver-free leaf
`src/core/llm/policy.py` (ADR-016 pattern) and never imports
`src.agents` — keeping the internal package graph acyclic (enforced by
`test_llm_layer_never_imports_agents_package` and the package cycle
test). Provider SDK imports are confined to the four client modules
(enforced by `test_boundary_hardening.py`); `base.py`, `errors.py` and
`policy.py` are provider-neutral (enforced by
`tests/test_architecture/test_llm_boundary.py`).

### 3.18 Cross-cutting Observability

`TraceContext` and `StructuredLogger` are available to every component via
`src/utils/logger.py`. They are part of the **shared utility surface** and
do not count as cross-layer violations.

---

## 4. Forbidden Dependencies

The following dependencies are **always forbidden**, regardless of layer.

### 4.1 Database

- **No component except `QueryExecutor` may read or write SQL Server.**
  Specifically forbidden:
  - `QuestionAnalyzer` may not import `pymssql`.
  - `SQLGenerator` may not import `pymssql`.
  - `SchemaRetriever` may not import `pymssql`.
  - `ResultAnalyzer` may not import `pymssql`.
  - `AnswerGenerator` may not import `pymssql`.
- `SchemaCatalog` may use a separate admin connection for introspection,
  not the per-request `QueryExecutor` connection.

### 4.2 LLM

- **No domain component may import a provider SDK directly.** Specifically
  forbidden:
  - `QuestionAnalyzer`, `QueryPlanner`, `SQLGenerator`, `SQLRepairer`,
    `AnswerGenerator` may not import `OpenAIClient`, `GeminiClient`,
    `GroqClient`, `OllamaClient`, `BaseLLMClient` or any vendor SDK.
  - They must go through `LLMRouter`.

### 4.3 AgentState

- **No component may import `AgentState`** or any of its sub-models
  (`SQLState`, `ExecutionState`, `ResponseState`, `ErrorState`,
  `RuntimeMetadata`, `RequestContext`, `ConversationContext`).
- Components receive typed value objects (`QuestionSpec`, `QueryPlan`,
  etc.) as parameters.
- The orchestrator is the only writer of `AgentState`.

### 4.4 Memory

- **No domain component may import `MemoryManager`.** Memory is the
  orchestrator's concern.
- `LLMRouter`, `SchemaCatalog`, `QueryExecutor`, `ResultAnalyzer`,
  `PresentationPlanner`, validators, and components MAY NOT import
  `MemoryManager`.

### 4.5 Logging

- **No component may own its own logger instance.** `get_logger(__name__)`
  is allowed inside `LLMRouter` (the router *is* infrastructure and
  produces its own diagnostic events) and inside cross-cutting modules
  (`src/utils/logger.py`). Domain components use `log_event(...)` only.
- **No component may call `logging.basicConfig` or configure handlers.**
  `configure_logging()` is called once at process startup.

### 4.6 Configuration

- **No component may import `os.environ` directly.** Configuration goes
  through `src/utils/config_loader.py` (existing) or a future typed
  config object. (This rule applies only to **new** components; the
  current `MemoryManager`, `DatabaseConnection`, `LLMClient`, etc. may
  continue to use `os.getenv` until they are refactored, since rewriting
  them is out of scope for Prompt 2A.)

### 4.7 API and Presentation

- **No domain or validator component may import `fastapi`,
  `pydantic.AskRequest`, `pydantic.AskResponse`, or any `src/api/*`
  module.** Components do not know about HTTP.
- **No component may import `streamlit`.**

### 4.8 Evaluation

- **No production component may import `evaluation.*`.** The evaluation
  harness is a test-only surface.

### 4.9 Forward references

- `QuestionAnalyzer` MUST NOT depend on `QueryPlanner`, `SQLGenerator`,
  `SQLRepairer`, `ResultAnalyzer`, `PresentationPlanner`,
  `AnswerGenerator`, `AnswerValidator`.
- `QueryPlanner` MUST NOT depend on `SQLGenerator` or any downstream
  component.
- `SchemaRetriever` MUST NOT depend on `SQLGenerator`.

These forward-reference rules prevent reverse-coupling that would
prevent the pipeline from being reorganized.

---

## 5. Dependency Matrix (Summary)

| Component           | DB | LLM | AgentState | Memory | Logging | API/UI | Eval |
|---------------------|:--:|:---:|:----------:|:------:|:-------:|:------:|:----:|
| QuestionAnalyzer    | no | yes (via router) | no | no | yes | no | no |
| SchemaCatalog       | yes (admin) | no | no | no | yes | no | no |
| SchemaRetriever     | no | no | no | no | yes | no | no |
| QueryPlanner        | no | yes (via router) | no | no | yes | no | no |
| SQLGenerator        | no | yes (via router) | no | no | yes | no | no |
| SQLValidator        | no | no | no | no | yes | no | no |
| SecurityValidator   | no | no | no | no | yes | no | no |
| SemanticValidator   | no | no | no | no | yes | no | no |
| ComplexityValidator | no | no | no | no | yes | no | no |
| SQLRepairer         | no | yes (via router) | no | no | yes | no | no |
| QueryExecutor       | yes | no | no | no | yes | no | no |
| ResultAnalyzer      | no | no | no | no | yes | no | no |
| PresentationPlanner | no | no | no | no | yes | no | no |
| AnswerGenerator     | no | yes (via router) | no | no | yes | no | no |
| AnswerValidator     | no | no | no | no | yes | no | no |
| MemoryManager       | no | no | no | (is) | yes | no | no |
| LLMRouter           | no | (is) | no | no | yes | no | no |

**Yes** means the dependency is allowed (often via a layer boundary —
e.g. "yes (via router)" means LLMRouter is the only allowed LLM touch
point).

---

## 6. State Mutation Rule

The single state mutation rule is:

```
Only the Orchestrator writes AgentState.
```

This is enforced by the convention in §6 of `interface-design.md` and is
**now enforced by the architecture tests in
`tests/test_architecture/test_single_writer.py`** (Prompt 2B).

- `test_orchestrator_is_the_only_state_writer_in_new_modules` walks the
  AST of every new module and asserts that `with_*` attribute calls
  (state-applier methods) only appear in `orchestrator.py`.
- `test_future_component_does_not_import_agent_state` ensures that
  `contracts.py` and `adapters.py` do not import `src.agents.state`.

The rule had **one exception** during the transition period: the
existing `TextToSQLAgent.ask()` continued to write `AgentState` directly
because it was the only orchestrator we had. In **Prompt 2B** a separate
`Orchestrator` class has been introduced and is the sole writer of
state. `TextToSQLAgent.ask()` is now a thin façade that delegates to the
`Orchestrator`.

---

## 7. Observability Dependency Rule

`log_event(...)`, `TraceContext`, and `get_logger(...)` are part of the
**shared utility surface**. They are available to every component
without violating the layering rules. They are **not** treated as
dependencies for architecture-test purposes.

The rule: components import from `src/utils/logger.py` only. They do not
import `logging`, `json`, or `os.path` for log output.

---

## 8. Internal vs External Adapter Rules

The LLM provider clients (`OpenAIClient`, `GeminiClient`, `GroqClient`,
`OllamaClient`) are external adapters that live behind `LLMRouter`.
Domain components may not import them. `LLMRouter` may import them and
owns their lifecycle.

`DatabaseConnection` is the SQL Server adapter. `QueryExecutor` (future)
will be the contract layer. `DatabaseConnection` is the current
implementation; it implements the `QueryExecutor` contract (this is
documented; the rename to `QueryExecutor` is deferred to Prompt 2B).

`MemoryManager` is the SQLite adapter. It is also the contract layer.

---

## 9. Architectural Cycle Prevention

The following three cycles are explicitly forbidden:

1. **Domain ↔ Infrastructure**: `SQLGenerator` → `QueryExecutor` →
   `ResultAnalyzer` → `AnswerGenerator` → ... → `SQLGenerator`. Components
   flow forward only; no component calls back into a previous component.

2. **Validator → Component → Validator**: validators do not call other
   components; they receive parsed inputs and return reports. (E.g.
   `SQLValidator` does not call `SemanticValidator`.)

3. **Orchestrator → Component → Orchestrator**: a component does not call
   back into the orchestrator. (Implemented in Prompt 2B once a real
   orchestrator exists.)

The dependency matrix in §5 enforces these.

---

## 10. Architecture Tests (Status: Implemented in Prompt 2B)

The dependency rules in this document are designed to be mechanically
enforceable. The following tests were **implemented in Sprint 2 Prompt
2B** and live in `tests/test_architecture/`:

1. **Static import-graph test** — `tests/test_architecture/test_dependencies.py`
   parses the AST of every component module and asserts the imports are
   within the allowed set per row in §3.

2. **AgentState import test** — `test_does_not_import_agent_state` on every
   future component module (contracts, adapters). Asserts that no
   component module imports `src.agents.state`.

3. **Provider SDK isolation test** — `test_does_not_import_provider_sdks`
   on `orchestrator.py` and other future component modules. Asserts that
   no domain component imports `OpenAIClient`, `GeminiClient`,
   `GroqClient`, `OllamaClient`, or any vendor SDK.

4. **API module boundary test** — `tests/test_architecture/test_dependencies.py::TestApiModuleDoesNotImportOrchestrator`
   asserts that the public API layer (`src/api/routes.py`) depends only
   on the public façade (`TextToSQLAgent`) and not on the internal
   `Orchestrator`.

5. **State mutation test** — `tests/test_architecture/test_single_writer.py`
   asserts that only `orchestrator.py` calls `state.with_*` methods
   among the new modules. Future component modules must not.

6. **Single-writer rule** — `test_future_component_does_not_import_agent_state`
   plus the AST walk in `test_orchestrator_is_the_only_state_writer_in_new_modules`
   enforce the rule from §6.

7. **SecurityDecision typing test** —
   `test_security_decision_is_typed_correctly` asserts that
   `SQLState.security_decision` is `Optional[SecurityDecision]`, not
   `Any`.

8. **Backward compatibility tests** — `tests/test_architecture/test_backward_compatibility.py`
   verifies that:
   - The `AskRequest` / `AskResponse` schemas are unchanged.
   - `state_to_api_response(state)` produces the documented dict format.
   - The CLI constructor signature is unchanged.
   - The evaluation adapter still normalizes the public response.
   - Session behavior is preserved end-to-end.

These tests are part of the standard `pytest` run. New components added
in future sprints must add the corresponding test entries to
`test_dependencies.py`.

**Hardened in Prompt 2C:**

- The internal import-graph cycle test no longer whitelists
  `src.agents ↔ src.core.database` / `src.memory`; the live graph is
  acyclic, so **any** future cycle fails the suite.
- `test_memory_manager_conforms` runtime-checks the existing
  `MemoryManager` implementation against `MemoryManagerProtocol`.
- `test_orchestrator_does_not_bypass_transition_to` now uses an AST
  check for direct `pipeline_stage` assignment (the previous substring
  check false-positived on `==` comparisons).
- The transitional default answer-generation callback moved from
  `orchestrator.py` to `src.agents.adapters.default_answer_fn`, so the
  Orchestrator source contains no prompt-construction logic.

---

## 11. Compatibility with Existing Code

The dependency rules apply to **new** components introduced in future
prompts. The current code (`TextToSQLAgent`, `SQLExtractor`,
`DatabaseConnection`, `MemoryManager`, `LLMClient`, etc.) remains in
place; it will be refactored incrementally as the new pipeline is built.

Specifically:

- `DatabaseConnection` already implements the `QueryExecutor` contract
  (§10 in `component-contracts.md`). It will be renamed/wrapped in
  Prompt 2B.
- `SQLSecurityValidator` already implements the `SecurityValidator`
  contract (§7 in `component-contracts.md`). No change required.
- `SQLValidator` (current) wraps `SQLSecurityValidator` for backward
  compatibility with `SQLExtractor`. In Prompt 2B this wrapper is
  removed when `SQLExtractor` is replaced by `SQLGenerator` +
  `SQLRepairer`.
- `MemoryManager` already implements the `MemoryManager` contract
  (§16 in `component-contracts.md`). No change required.
- `LLMClient` is now a **task-bound compatibility adapter** over
  `LLMRouter` (Sprint 3B). The legacy transitive dependency
  `SQLExtractor → LLMClient → OpenAI SDK` is removed: the extractor
  accepts any `generate(prompt) -> str` client and imports no LLM
  module. The adapter remains at the SQL-extraction and answer seams
  until `SQLGenerator`/`AnswerGenerator` land — that seam is the
  remaining LLM migration debt.

The existing `TextToSQLAgent` violates several rules (it constructs
infra directly, writes `AgentState`, holds a DB connection for startup).
These violations are accepted as **transition debt** to be paid down in
Prompt 2B.

---

## 12. Open Questions

| # | Question | Deferred to |
|---|----------|-------------|
| Q1 | Should `SQLGenerator` and `SQLRepairer` share a base interface? | Prompt 2B |
| Q2 | Should the `MemoryManager` typed turn model be defined now? | Sprint 11 |
| Q3 | Should `LLMRouter` expose both sync and async APIs? | Sprint 6 |
| Q4 | Should `AnswerValidator` have an LLM-judge mode? | Sprint 8 |

---

## 13. Summary

This document is the **canonical source** for "who may depend on what".
Any future prompt that adds a component or refactors an existing one must
update this document and `component-contracts.md` together. ADR-012
records this rule.