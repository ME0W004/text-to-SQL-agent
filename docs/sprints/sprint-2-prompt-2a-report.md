# Sprint 2 — Prompt 2A Report: Interface Audit & Contract Design

**Date**: 2026-08-31
**Sprint**: 2 — Prompt 2A
**Status**: Complete

---

## 1. Executive Summary

Prompt 2A is a **design-only** step. The repository has been audited
component-by-component, the current implementation's boundaries have
been mapped to the approved Target Architecture and ADRs, and a formal
contract set has been produced for every target component.

The major findings:

1. **The existing `AgentState` is already the integration spine.** Sprint 2
   Prompt 1A/1B/1C made every code path flow through `AgentState` →
   `state_to_api_response()`. The 209-test baseline proves the boundary
   holds. The contract work in Prompt 2A formalizes this.

2. **The current `TextToSQLAgent` is a transition-state God Object.**
   It owns infrastructure (DB, LLM, memory, extractor, prompts), writes
   `AgentState`, calls the LLM, executes SQL, generates natural-language
   answers, and translates columns. The Target Architecture splits this
   into 17 focused components, each with a documented contract.

3. **Security Core from Sprint 1 must remain authoritative.** ADR-004 is
   preserved verbatim. `SecurityValidator` is the only component allowed
   to make security decisions; `QueryExecutor` integrates with it as a
   documented pre-condition.

4. **The single-writer rule for `AgentState` is the keystone.** No
   component imports or mutates `AgentState`. Components return typed
   results; the Orchestrator writes the state. ADR-013 records this.

5. **LLM access must be funneled through `LLMRouter`.** ADR-011 is
   preserved. Domain components never import provider SDKs.

6. **The database boundary is single and explicit.** ADR-006 is preserved.
   `QueryExecutor` is the only component that may read SQL Server.

The contracts produced in this prompt are documented in three files:

- `docs/contracts/interface-design.md` — layering, classification,
  state interaction strategy, error contract strategy, observability
  boundary, contract versioning, sync/async decisions.
- `docs/contracts/component-contracts.md` — per-component contracts
  (17 components + 2 cross-cutting adapters).
- `docs/architecture/dependency-rules.md` — allowed/forbidden
  dependencies, layering rules, future architecture tests.

Four new ADRs were added:

- ADR-012: Component Contract Authority
- ADR-013: Single Writer of AgentState
- ADR-014: Validator Separation (SQL / Security / Semantic / Complexity)
- ADR-015: Typed Error Code Registry

The public HTTP API (`POST /api/v1/ask`) and the existing 209 tests are
unchanged. No runtime code was modified.

---

## 2. Files Inspected

### Documentation (mandatory references)
- `docs/sprints/sprint-0-report.md`
- `docs/sprints/sprint-1-report.md`
- `docs/sprints/sprint-2-prompt-1a-report.md`
- `docs/sprints/sprint-2-prompt-1b-report.md`
- `docs/sprints/sprint-2-prompt-1c-report.md`
- `docs/architecture/current-architecture.md`
- `docs/architecture/current-components.md`
- `docs/architecture/current-data-flow.md`
- `docs/architecture/target-architecture.md`
- `docs/architecture/target-data-flow.md`
- `docs/architecture/component-responsibilities.md`
- `docs/architecture/agent-state-design.md`
- `docs/contracts/agent-state.md`
- `docs/contracts/component-contracts.md`
- `docs/migration/migration-plan.md`
- `docs/adr/ADR-001..011`
- `docs/audit/*.md` (all 11 files)

### Source code (every file in `src/`)
- `src/agents/state.py` (713 lines)
- `src/agents/text_to_sql_agent.py` (249 lines)
- `src/agents/__init__.py`
- `src/core/sql_extractor/extractor.py` (93 lines)
- `src/core/sql_extractor/validator.py` (27 lines)
- `src/core/sql_extractor/few_shots.py` (63 lines)
- `src/core/security/sql_security.py` (176 lines)
- `src/core/database/connection.py` (274 lines)
- `src/core/database/models.py` (139 lines)
- `src/core/llm/client.py` (51 lines)
- `src/core/llm/base.py` (14 lines)
- `src/core/llm/openai_client.py` (42 lines)
- `src/core/llm/groq_client.py` (34 lines)
- `src/core/llm/gemini_client.py` (31 lines)
- `src/core/llm/ollama_client.py` (33 lines)
- `src/core/prompts.py` (108 lines)
- `src/core/column_mapper.py` (53 lines)
- `src/core/column_mapper_updater.py` (91 lines)
- `src/core/schema_hash.py` (36 lines)
- `src/memory/manager.py` (86 lines)
- `src/api/main.py` (44 lines)
- `src/api/routes.py` (77 lines)
- `src/api/models.py` (23 lines)
- `src/web/app.py` (336 lines)
- `src/utils/logger.py` (178 lines)
- `src/utils/exceptions.py` (25 lines)
- `src/utils/config_loader.py` (44 lines)

### Entry points
- `run.py` (42 lines)
- `run_api.py` (11 lines)
- `run_ui.py` (14 lines)

### Evaluation
- `evaluation/adapters.py` (147 lines)
- `evaluation/models.py` (88 lines)
- `evaluation/runner.py` (230 lines)
- `evaluation/metrics.py` (83 lines)
- `evaluation/run.py` (132 lines)

### Tests (verified passing baseline)
- `tests/test_agents/test_state.py`
- `tests/test_agents/test_state_isolation.py`
- `tests/test_agents/test_state_boundaries.py`
- `tests/test_agents/test_agent_error_paths.py`
- `tests/test_database/test_safe_execution.py`
- `tests/test_evaluation/test_adapters.py`
- `tests/test_evaluation/test_cli.py`
- `tests/test_evaluation/test_metrics.py`
- `tests/test_evaluation/test_runner.py`
- `tests/test_integration/test_sprint1_path.py`
- `tests/test_observability/test_integration.py`
- `tests/test_observability/test_logging.py`
- `tests/test_security/test_sql_security.py`
- `tests/test_security/test_database_boundary.py`

---

## 3. Current Component Audit

The following components were inspected. For brevity the full per-component
table is in
[`docs/contracts/interface-design.md`](../../contracts/interface-design.md)
§4. Key findings:

### 3.1 TextToSQLAgent (`src/agents/text_to_sql_agent.py`)

- **Current responsibility**: end-to-end orchestration. Creates State,
  resolves session, fetches history, generates SQL via `SQLExtractor`,
  executes via `DatabaseConnection.execute_query`, filters/translates
  rows, calls LLM for natural-language answer, persists messages to
  `MemoryManager`, returns API-shaped dict.
- **Public methods**: `__init__()`, `_filter_and_translate_rows()`,
  `ask(question, session_id)`, `close()`.
- **Inputs**: `question: str`, `session_id: Optional[str]`.
- **Outputs**: dict matching `AskResponse` (backward-compatible).
- **Dependencies**: `DatabaseConnection`, `SQLExtractor`, `LLMClient`,
  `SystemPrompts`, `MemoryManager`, `COLUMN_MAP`, all of `src/utils/*`.
- **Side effects**: DB connection at startup (immediately closed),
  LLM calls (1 for SQL generation, 1 for answer, plus N repair
  retries), SQLite read/write, log events.
- **State access**: reads `AgentState` (sole writer today).
- **LLM access**: direct via `LLMClient`.
- **DB access**: owns `DatabaseConnection`, calls
  `execute_query()`.
- **Persistence access**: `MemoryManager`.
- **Observability access**: `log_event()`, `TraceContext`.
- **Known coupling**: high. Holds all infrastructure references, writes
  AgentState, calls LLM directly, executes SQL, formats UI output.
- **Problems**: violates the future single-writer rule for AgentState
  (acceptable during transition; will be replaced by an Orchestrator in
  Prompt 2B); performs row filtering/translation that belongs in
  `PresentationPlanner`; calls LLM directly via `LLMClient` rather than
  via the future `LLMRouter`; constructs prompts via `SystemPrompts`,
  violating the principle that prompts are component responsibility.
- **Target status**: split. `TextToSQLAgent` becomes a thin façade
  invoking the new `Orchestrator`. The current implementation is
  preserved until Prompt 2B.

### 3.2 SQLExtractor (`src/core/sql_extractor/extractor.py`)

- **Current responsibility**: prompt the LLM, clean the SQL output, retry
  validation up to `max_retries`.
- **Public methods**: `__init__(llm_client)`, `_clean_sql()`,
  `extract(question, history, max_retries)`.
- **Dependencies**: `LLMClient`, `SystemPrompts`, `SQLValidator`.
- **LLM access**: yes (direct).
- **Side effects**: LLM calls; log events.
- **Known coupling**: contains prompt-construction knowledge and
  validation logic in one class.
- **Problems**: mixes generation, cleaning, and validation; repair loop is
  unconditional; no repair budget tracked.
- **Target status**: replaced by `SQLGenerator` + `SQLRepairer`. Current
  implementation preserved until Prompt 2B.

### 3.3 SQLValidator (`src/core/sql_extractor/validator.py`)

- **Current responsibility**: thin compatibility wrapper that delegates to
  `SQLSecurityValidator`. Keeps the bool/exception API for
  `SQLExtractor`.
- **Target status**: replaced by `SQLValidator` (syntax only) +
  `SecurityValidator` + `SemanticValidator` + `ComplexityValidator`. The
  current wrapper is preserved for backward compatibility and removed in
  Prompt 2B.

### 3.4 SQLSecurityValidator / Security Core (`src/core/security/sql_security.py`)

- **Current responsibility**: AST-based fail-closed SQL Server policy.
- **Public methods**: `parse_sql()`, `validate()`,
  `validate_sql_security()` module-level function.
- **Inputs**: raw SQL string, optional `SecurityPolicy`.
- **Outputs**: `SecurityDecision` (or raises `SQLSecurityError`).
- **Side effects**: log events only.
- **Deterministic**: yes. **Security-sensitive**: yes (critical).
- **Target status**: implementation of `SecurityValidator` contract
  (§7 in `component-contracts.md`). No change required.

### 3.5 DatabaseConnection (`src/core/database/connection.py`)

- **Current responsibility**: pymssql driver wrapper with
  `ExecutionPolicy`, fresh connection/cursor per query, typed error
  mapping.
- **Public methods**: `__init__()`, `connect()`, `close()`,
  `execute_query()`, `execute_non_query()`,
  class methods `_is_timeout`, `_map_error`,
  `from_environment()` on `ExecutionPolicy`.
- **Dependencies**: `pymssql`, `SQLSecurityValidator`, `ExecutionPolicy`,
  `QueryExecutionError` (existing), `DatabaseError`/`DatabaseErrorCode`.
- **Side effects**: opens/closes connections; log events.
- **Deterministic**: yes (modulo DB). **Security-sensitive**: yes.
- **Target status**: implementation of `QueryExecutor` contract
  (§10 in `component-contracts.md`). The current integration of
  `SQLSecurityValidator.validate()` before `execute` is documented and
  preserved.

### 3.6 ExecutionPolicy (`src/core/database/connection.py`)

- **Current responsibility**: validated resource limits for one DB
  operation.
- **Target status**: unchanged. Implementation of `ExecutionPolicy`
  contract. Re-exported via `QueryExecutor`.

### 3.7 MemoryManager (`src/memory/manager.py`)

- **Current responsibility**: SQLite-backed sessions and messages.
- **Public methods**: `create_session()`, `session_exists()`,
  `add_message()`, `get_history()`, `clear_session()`.
- **Dependencies**: `sqlite3`, `os`, `uuid`.
- **Side effects**: SQLite reads/writes.
- **Target status**: implementation of `MemoryManager` contract
  (§16). No change required in this prompt. Sprint 11 may add typed
  turn model and provenance.

### 3.8 LLMClient (`src/core/llm/client.py`)

- **Current responsibility**: thin facade over `OpenAIClient`.
- **Public methods**: `__init__()`, `is_internet_available()`,
  `generate(prompt)`, `is_available()`.
- **Dependencies**: `OpenAIClient`, `requests`.
- **Target status**: replaced by `LLMRouter`. The provider clients
  (`OpenAIClient`, `GeminiClient`, `GroqClient`, `OllamaClient`) remain
  as the LLMRouter's external adapters.

### 3.9 ColumnMapper (`src/core/column_mapper.py`)

- **Current responsibility**: static Persian translation map.
- **Target status**: replaced by `SchemaCatalog.labels`. The current map
  is the static fallback.

### 3.10 ColumnMapperUpdater (`src/core/column_mapper_updater.py`)

- **Current responsibility**: regenerate `column_mapper.py` when schema
  hash changes.
- **Problems**: writes Python source; buggy relative path; auto-update
  can write into `scripts/src/core/`.
- **Target status**: replaced by `SchemaCatalog.refresh()`. Out of scope
  for Prompt 2B.

### 3.11 TraceContext (`src/utils/logger.py`)

- **Current responsibility**: request correlation (contextvar).
- **Target status**: unchanged. Cross-cutting infrastructure, used by
  every component via `get_trace_context()` / `set_trace_context()`.

### 3.12 StructuredLogger (`src/utils/logger.py`)

- **Current responsibility**: JSON-formatted logs, sensitive-data
  redaction, rotating file handler.
- **Target status**: unchanged. Cross-cutting infrastructure.

### 3.13 EvaluationAgentAdapter (`evaluation/adapters.py`)

- **Current responsibility**: stable adapter around
  `TextToSQLAgent.ask()`.
- **Target status**: unchanged in Prompt 2A. Continues to consume
  `state_to_api_response(state)` dict. Not a target component.

### 3.14 API layer (`src/api/routes.py`, `src/api/main.py`, `src/api/models.py`)

- **Current responsibility**: HTTP envelope.
- **Target status**: unchanged. `/api/v1/ask` is frozen.

### 3.15 CLI (`run.py`)

- **Current responsibility**: thin shell over `agent.ask()`.
- **Target status**: unchanged. Not a target component.

### 3.16 Streamlit UI (`src/web/app.py`)

- **Current responsibility**: RTL chat UI consuming API.
- **Target status**: unchanged. Not a target component.

### 3.17 SystemPrompts (`src/core/prompts.py`)

- **Current responsibility**: SQL, correction, natural-response prompt
  templates.
- **Target status**: prompts become the responsibility of the component
  that uses them (`SQLGenerator`, `SQLRepairer`, `AnswerGenerator`).
  `SystemPrompts` is replaced by per-component prompt templates.

---

## 4. Responsibility Findings

### 4.1 Overlapping responsibilities

| Overlap | Current owner | Proposed owner | Reason | Migration implication |
|--------|---------------|----------------|--------|-----------------------|
| Prompt construction for SQL | `SystemPrompts` + `SQLExtractor` | `SQLGenerator` + `SQLRepairer` | Each LLM-using component owns its prompts | Move templates into each component module; `SystemPrompts` deprecated |
| SQL cleaning | `SQLExtractor._clean_sql()` | `SQLGenerator` (post-LLM step) | Cleaning is part of generation | Delete `SQLExtractor` |
| Validation retry loop | `SQLExtractor.extract()` retry | `SQLRepairer` bounded by `SQLState.max_correction_attempts` | Repair is a separate component with its own contract | Wire `SQLRepairer` after `SQLValidator` in the Orchestrator |
| Column translation | `_filter_and_translate_rows()` in `TextToSQLAgent` | `PresentationPlanner` (uses `SchemaSnapshot.labels`) | Presentation is a separate component | Move translation to `PresentationPlanner`; remove `_filter_and_translate_rows()` |
| Persian natural-response prompt | `SystemPrompts.natural_response_prompt()` | `AnswerGenerator` | Answer generation owns its prompt | Move template into `AnswerGenerator` |
| Error message sanitization | Catch blocks in `TextToSQLAgent.ask()` | Orchestrator via `state_to_api_response()` + `ErrorState.user_message` | State is the canonical error carrier | Already true today; no change |
| LLM provider selection | `LLMClient.generate` (single provider) | `LLMRouter` (multi-provider) | ADR-011 | Add `LLMRouter`; `LLMClient` becomes its adapter |
| Schema information | `FULL_SCHEMA` in `core/database/models.py`, `COLUMN_MAP` | `SchemaCatalog` | ADR-002 | Replace static files with live snapshots |
| Memory + conversation state | `MemoryManager.get_history()` returning dict list | `MemoryManager.get_history()` returning typed `Turn` tuple | ADR-009 (typed turns + provenance) | Sprint 11 |
| `DB_PORT` validation | `config_loader.load_config()` | `ConfigurationError` raised at startup | Already typed | No change |

### 4.2 Coupling that must be broken

| Coupling | Evidence | Target |
|----------|----------|--------|
| `TextToSQLAgent` constructs `DatabaseConnection`, `LLMClient`, `SQLExtractor`, `MemoryManager`, `SystemPrompts` | `text_to_sql_agent.py:44-56` | Orchestrator receives these as injected dependencies (constructor params) |
| `TextToSQLAgent.ask()` writes `AgentState` directly | `text_to_sql_agent.py:107,113,123,127,133,140,168` | Same pattern, but moved into `Orchestrator.run()`; `TextToSQLAgent` becomes a façade |
| `TextToSQLAgent.ask()` calls LLM directly | `text_to_sql_agent.py:155` | Orchestrator calls `LLMRouter` |
| `TextToSQLAgent.ask()` owns row filtering | `text_to_sql_agent.py:139` | `PresentationPlanner` |
| `DatabaseConnection.execute_query()` performs security validation inline | `connection.py:166-181` | Preserved as documented integration; `QueryExecutor` contract states this pre-condition |
| `LLMClient` only supports OpenAI | `client.py:11-51` | `LLMRouter` with provider fallback |
| `MemoryManager.get_history()` returns `list[dict]` with no typed schema | `manager.py:63-73` | Sprint 11 typed `Turn` |
| `SystemPrompts` mixes prompt responsibility across components | `prompts.py` | Per-component prompt templates |

### 4.3 Hidden dependencies

- `TextToSQLAgent` holds `COLUMN_MAP` via dynamic import — a hidden
  source-of-truth dependency on a generated Python file. Migrated to
  `SchemaCatalog.labels`.
- `DatabaseConnection` connects at startup and immediately closes,
  leaving schema-hash logic as a side path. Migrated to
  `SchemaCatalog.refresh()`.
- `SQLExtractor` imports `LLMClient` directly and is the only component
  that talks to the LLM. After splitting, multiple components will need
  LLM access; they must go through `LLMRouter`.

---

## 5. Target Interface Inventory

The full inventory is in `docs/contracts/interface-design.md` §4. Summary:

| # | Component | Layer | ADR | New / Existing |
|---|-----------|-------|-----|----------------|
| 1 | QuestionAnalyzer | Domain (LLM) | ADR-001 | New |
| 2 | SchemaCatalog | Infrastructure | ADR-002 | New |
| 3 | SchemaRetriever | Domain | ADR-002 | New |
| 4 | QueryPlanner | Domain (LLM) | ADR-003 | New |
| 5 | SQLGenerator | Domain (LLM) | ADR-001 | New (replaces `SQLExtractor`) |
| 6 | SQLValidator | Validator | ADR-004 | New (replaces `core/sql_extractor/validator.py`) |
| 7 | SecurityValidator | Validator / Security | ADR-004 | Implemented by `SQLSecurityValidator` |
| 8 | SemanticValidator | Validator | ADR-005 | New |
| 9 | ComplexityValidator | Validator | ADR-004 follow-up | New |
| 10 | QueryExecutor | Infrastructure | ADR-006 | Implemented by `DatabaseConnection` |
| 11 | ResultAnalyzer | Domain | ADR-007 | New |
| 12 | PresentationPlanner | Domain | ADR-008 | New (replaces `_filter_and_translate_rows`) |
| 13 | AnswerGenerator | Domain (LLM) | ADR-001 | New (replaces inline LLM call in agent) |
| 14 | AnswerValidator | Validator | ADR-005 | New |
| 15 | SQLRepairer | Domain (LLM) | ADR-001 | New (replaces retry loop in `SQLExtractor`) |
| 16 | MemoryManager | Infrastructure | ADR-009 | Implemented by `src/memory/manager.py` |
| 17 | LLMRouter | Infrastructure / Adapter | ADR-011 | New (replaces `LLMClient`) |
| - | TraceContext | Cross-cutting | Sprint 1 P4 | Implemented |
| - | StructuredLogger | Cross-cutting | Sprint 1 P4 | Implemented |

---

## 6. Interface Contracts

The full per-component contract is in
[`docs/contracts/component-contracts.md`](../../contracts/component-contracts.md).
For each of the 17 components the contract specifies:

- Purpose
- Input (typed parameters)
- Output (typed value objects)
- Errors (typed, with codes from the registry)
- Side Effects
- Dependencies (may use)
- Forbidden Dependencies
- State Interaction (reads / writes / returns)
- Deterministic / LLM
- Security Sensitivity

Example: `SQLGenerator` — see `component-contracts.md` §5.

### 6.1 Typed value objects introduced

The contracts introduce typed value objects for inter-component data:

- `QuestionSpec`, `Entity`, `Filter`, `Metric`, `TimeConstraint`,
  `Reference`, `AmbiguityReport`, `Evidence`
- `SchemaSnapshot`, `TableDescriptor`, `Relationship`,
  `BusinessSemantics`, `SchemaVersion`
- `SchemaContext`, `TableRef`, `ColumnRef`, `JoinHint`
- `QueryPlan`, `Join`, `Dimension`, `Metric`, `Filter`, `SortKey`,
  `OutputRequirements`
- `SQLCandidate`, `ParsedAST`
- `ValidationReport`, `SyntaxError`
- `SecurityDecision` (already exists in `src/core/security/sql_security.py`)
- `SemanticReport`, `SemanticViolation`
- `ComplexityReport`, `ComplexityBudget`
- `QueryResult`
- `ResultProfile`, `ShapeDescriptor`, `ColumnType`, `AggregateRef`
- `PresentationPlan`, `FormatSpec`, `LocalePolicy`
- `AnswerDraft`, `Claim`, `Evidence`
- `ValidatedAnswer`
- `Turn` (Sprint 11; not defined in this prompt)
- `LLMResult`, `Usage`, `LLMPolicy`, `LLMBudget`

These will be defined as frozen dataclasses when the components are
implemented. Defining them here now is the value of having a stable
contract — the future implementer knows exactly what shape to produce
and consume.

---

## 7. Dependency Rules

The full dependency rules are in
[`docs/architecture/dependency-rules.md`](../../architecture/dependency-rules.md).

Highlights:

- **Database boundary**: only `QueryExecutor` may import `pymssql`. No
  other component may read SQL Server.
- **LLM boundary**: only `LLMRouter` may import provider SDKs. No domain
  component may import `OpenAIClient`, `GeminiClient`, `GroqClient`, or
  `OllamaClient`.
- **AgentState boundary**: no component may import `AgentState` or its
  sub-models. The orchestrator is the sole writer (ADR-013).
- **Memory boundary**: no domain component may import `MemoryManager`.
- **Logging boundary**: components use `log_event(...)`; they do not own
  logger instances (except `LLMRouter`, which is infrastructure).
- **Forward-reference prohibition**: `QuestionAnalyzer` → ... →
  `AnswerValidator` flows forward only.

The dependency matrix in
`docs/contracts/component-contracts.md` §19 is the basis for future
architecture tests (defined in Prompt 2B).

---

## 8. Error Contract Strategy

The error strategy is in `docs/contracts/interface-design.md` §7.

Highlights:

- Existing typed exceptions (`SQLValidationError`, `SQLSecurityError`,
  `QueryExecutionError`, `DatabaseError`, `ConfigurationError`,
  `LLMError`) are preserved.
- New typed exceptions are additive (no breaking changes):
  - `QuestionAnalysisError`
  - `SchemaError`
  - `PlanningError`
  - `SQLGenerationError`
  - `RepairError`
  - `ResultAnalysisError`
  - `PresentationError`
  - `AnswerGenerationError`
  - `AnswerValidationError`
  - `MemoryError`
  - `LLMRouterError`
- Every typed error carries `code`, `message`, `user_message`, `stage`,
  `category`, `retryable`, `cause`.
- The canonical code registry (`interface-design.md` §7.4) is
  namespaced: `QA.`, `SCH.`, `PLN.`, `GEN.`, `PAR.`, `VAL.`, `SEC.`,
  `SEM.`, `CMX.`, `RPR.`, `EXE.`, `RES.`, `PRE.`, `ANS.`, `ANV.`,
  `MEM.`, `ROU.`, `CFG.`
- Retry policy is enforced by the orchestrator; components do not retry.

ADR-015 records the typed-error-code-registry rule.

---

## 9. State Interaction Strategy

The state interaction strategy is in `docs/contracts/interface-design.md`
§6.

Summary:

- **Only the Orchestrator writes `AgentState`.** ADR-013.
- Components receive typed value objects as parameters.
- Components return typed results.
- The Orchestrator applies results via state-applier convenience methods
  (existing ones plus new ones for the future fields
  `question_analysis`, `schema_context`, `query_plan`,
  `result_profile`, `presentation_plan`, `validated_answer`).
- No component imports `AgentState` or any of its sub-models.

State-applier methods to be added in Prompt 2B (no implementation in this
prompt):

```text
with_question_analysis(spec: QuestionSpec) -> AgentState
with_schema_context(context: SchemaContext) -> AgentState
with_query_plan(plan: QueryPlan) -> AgentState
with_result_profile(profile: ResultProfile) -> AgentState
with_presentation_plan(plan: PresentationPlan) -> AgentState
with_validated_answer(answer: ValidatedAnswer) -> AgentState
```

---

## 10. Side-effect Matrix

See `docs/contracts/component-contracts.md` §18. Summary:

- Only `QueryExecutor` reads SQL Server.
- No component writes SQL Server.
- No component other than `LLMRouter` opens a network socket.
- Only `MemoryManager` reads/writes SQLite.
- Only `TraceContext`/`StructuredLogger` and `LLMRouter` produce logs.
- No component other than the Orchestrator writes `AgentState`.

---

## 11. Dependency Matrix

See `docs/contracts/component-contracts.md` §19. Summary:

| Component | DB | LLM | AgentState | Memory | Logging | API/UI | Eval |
|-----------|:--:|:---:|:----------:|:------:|:-------:|:------:|:----:|
| QuestionAnalyzer | no | yes (router) | no | no | yes | no | no |
| SchemaCatalog | yes (admin) | no | no | no | yes | no | no |
| SchemaRetriever | no | no | no | no | yes | no | no |
| QueryPlanner | no | yes (router) | no | no | yes | no | no |
| SQLGenerator | no | yes (router) | no | no | yes | no | no |
| SQLValidator | no | no | no | no | yes | no | no |
| SecurityValidator | no | no | no | no | yes | no | no |
| SemanticValidator | no | no | no | no | yes | no | no |
| ComplexityValidator | no | no | no | no | yes | no | no |
| SQLRepairer | no | yes (router) | no | no | yes | no | no |
| QueryExecutor | yes | no | no | no | yes | no | no |
| ResultAnalyzer | no | no | no | no | yes | no | no |
| PresentationPlanner | no | no | no | no | yes | no | no |
| AnswerGenerator | no | yes (router) | no | no | yes | no | no |
| AnswerValidator | no | no | no | no | yes | no | no |
| MemoryManager | no | no | no | (is) | yes | no | no |
| LLMRouter | no | (is) | no | no | yes | no | no |

The matrix is acyclic by construction. Forward references are forbidden.
ADR-012 and ADR-013 lock these rules.

---

## 12. Public API Compatibility

The `/api/v1/ask` contract is **frozen** in this prompt:

- `AskRequest` Pydantic model: `question: str`, `session_id: Optional[str]`.
- `AskResponse` Pydantic model: `success`, `question`, `answer`, `sql`,
  `row_count`, `rows`, `error`, `session_id`.
- The response dict returned by `state_to_api_response(state)` matches
  the existing format (verified by 209 tests).

Internal contracts may change freely as long as the public contract is
preserved. CLI and Streamlit consume the same dict; they remain
unchanged.

`EvaluationAgentAdapter` consumes the same dict; it remains unchanged.

---

## 13. Architectural Decisions

Four new ADRs were created:

- **ADR-012: Component Contract Authority.** The contract documents in
  `docs/contracts/component-contracts.md` and
  `docs/architecture/dependency-rules.md` are authoritative. Any
  deviation requires updating these documents together with an ADR.

- **ADR-013: Single Writer of AgentState.** Only the Orchestrator
  writes `AgentState`. Components return typed results.

- **ADR-014: Validator Separation.** Four separate validator
  interfaces (SQLValidator, SecurityValidator, SemanticValidator,
  ComplexityValidator) with single responsibilities. SecurityValidator
  remains authoritative (ADR-004 preserved).

- **ADR-015: Typed Error Code Registry.** All components raise errors
  from the typed hierarchy with codes from the registry in
  `interface-design.md` §7.4. Existing exception types are preserved.

The existing ADRs (ADR-001..011) remain in force.

---

## 14. Open Questions

| # | Question | Resolution |
|---|----------|------------|
| Q1 | Should `SQLGenerator` and `SQLRepairer` share a base interface? | Deferred to Prompt 2B. |
| Q2 | Should `MemoryManager` typed turn model be defined now? | Deferred to Sprint 11 (ADR-009). |
| Q3 | Should `LLMRouter` expose both sync and async APIs? | Deferred to Sprint 6 (LLM routing). Sync-only in current contracts. |
| Q4 | Should `AnswerValidator` have an LLM-judge mode? | Deferred to Sprint 8 (Answer). Deterministic only in current contracts. |
| Q5 | Should `ComplexityValidator` be a separate component or part of `SecurityValidator`? | Resolved here: separate component, ADR-014. |
| Q6 | Should `LLMRouter` be the only component allowed to call providers? | Resolved here: yes, ADR-011 + ADR-013 preserved. |
| Q7 | Should `Token_usage` be part of `RuntimeMetadata` or a separate `LLMUsage` record? | Already in `RuntimeMetadata.token_usage` (Sprint 2 Prompt 1B); no change. |
| Q8 | Should the Orchestrator be a class or a function? | Deferred to Prompt 2B. |
| Q9 | How should the orchestrator update `RuntimeMetadata.attempt_counters` per stage? | Deferred to Prompt 2B. |
| Q10 | Should `SchemaCatalog.refresh()` be auto-invoked at startup or admin-only? | Deferred to Sprint 5. |

---

## 15. Prompt 2B Implementation Plan

Prompt 2B will introduce the **Orchestrator** class and the typed value
objects from §6.1. The plan is:

1. **Define typed value objects** in `src/agents/state.py` or a new
   `src/agents/contracts.py` module:
   - `QuestionSpec`, `Entity`, `Filter`, `Metric`, `TimeConstraint`,
     `Reference`, `AmbiguityReport`, `Evidence`.
   - `SchemaSnapshot`, `TableDescriptor`, `Relationship`,
     `BusinessSemantics`, `SchemaVersion`.
   - `SchemaContext`, `TableRef`, `ColumnRef`, `JoinHint`.
   - `QueryPlan`, `Join`, `Dimension`, `SortKey`, `OutputRequirements`.
   - `SQLCandidate`, `ParsedAST`, `SyntaxError`.
   - `ValidationReport`.
   - `SemanticReport`, `SemanticViolation`.
   - `ComplexityReport`, `ComplexityBudget`.
   - `QueryResult`.
   - `ResultProfile`, `ShapeDescriptor`, `ColumnType`, `AggregateRef`.
   - `PresentationPlan`, `FormatSpec`, `LocalePolicy`.
   - `AnswerDraft`, `Claim`.
   - `ValidatedAnswer`.
   - `LLMResult`, `Usage`, `LLMPolicy`, `LLMBudget`.
   - New typed errors from §7.

2. **Add `AgentState` state-applier convenience methods**:
   - `with_question_analysis(spec)`
   - `with_schema_context(context)`
   - `with_query_plan(plan)`
   - `with_result_profile(profile)`
   - `with_presentation_plan(plan)`
   - `with_validated_answer(answer)`
   - Add `result_profile` and `presentation_plan` placeholders as
     `Optional[Any]` first; type them when components exist.

3. **Introduce `Orchestrator` class** in `src/agents/orchestrator.py`
   that wires the pipeline:
   - `Orchestrator.run(state: AgentState) -> AgentState`
   - Receives `QuestionAnalyzer`, `SchemaCatalog`, `SchemaRetriever`,
     `QueryPlanner`, `SQLGenerator`, `SQLValidator`, `SecurityValidator`,
     `SemanticValidator`, `ComplexityValidator`, `SQLRepairer`,
     `QueryExecutor`, `ResultAnalyzer`, `PresentationPlanner`,
     `AnswerGenerator`, `AnswerValidator`, `MemoryManager`, `LLMRouter` as
     injected dependencies.
   - Updates `state.pipeline_stage` at each transition.
   - Handles errors via state-applier methods (`with_*_failed`).

4. **Preserve backward compatibility**: `TextToSQLAgent.ask()` becomes
   a thin façade that builds an `AgentState` and delegates to
   `Orchestrator.run(state)`, then returns `state_to_api_response(state)`.

5. **Add architecture tests** (per `dependency-rules.md` §10):
   - No component module imports `src.agents.state`.
   - No domain component imports `pymssql`, `OpenAIClient`,
     `GeminiClient`, `GroqClient`, `OllamaClient`.
   - No component method calls `state.with_*` outside the Orchestrator.

6. **Add 50+ new tests** covering:
   - Orchestrator happy path (all stages transition correctly).
   - Each validator's report shape.
   - Each typed value object's frozen semantics.
   - Error propagation through the state.

7. **Verify** the existing 209 tests still pass and the new tests pass.

Prompt 2B does **not** implement QuestionAnalyzer, SchemaCatalog, or
LLMRouter in this prompt — those arrive in Sprints 3-6. Prompt 2B builds
the **plumbing** so those components can be slotted in without breaking
the pipeline.

---

## 16. Files Created

```text
docs/contracts/interface-design.md              # Created — layering, classification, state interaction, error strategy, observability, versioning, sync/async
docs/contracts/component-contracts.md          # Updated — per-component contracts for 17 components + 2 cross-cutting adapters
docs/architecture/dependency-rules.md          # Created — allowed/forbidden dependencies, layering, future architecture tests
docs/architecture/component-responsibilities.md # Updated — non-responsibilities aligned with contracts
docs/adr/ADR-012-component-contract-authority.md   # Created
docs/adr/ADR-013-single-writer-of-agent-state.md  # Created
docs/adr/ADR-014-validator-separation.md            # Created
docs/adr/ADR-015-typed-error-code-registry.md      # Created
docs/sprints/sprint-2-prompt-2a-report.md          # Created (this file)
```

## 17. Files Modified

None in `src/`, `evaluation/`, or any runtime file. The contract documents
updated (`component-contracts.md`, `component-responsibilities.md`) are
documentation only.

## 18. Code Changes

None. No runtime code was modified. No tests were added or modified. The
existing 209-test suite still passes.

---

## Definition of Done Verification

| Criterion | Status |
|-----------|--------|
| Current component boundaries audited | ✅ (§3) |
| Responsibility overlaps identified | ✅ (§4) |
| Missing target boundaries identified | ✅ (§5) |
| Target components classified | ✅ (§5, `interface-design.md` §4-5) |
| Responsibilities documented | ✅ (`interface-design.md` §4, `component-responsibilities.md`) |
| Non-responsibilities documented | ✅ (`component-responsibilities.md`) |
| Dependency rules defined | ✅ (`dependency-rules.md`) |
| QuestionAnalyzer contract | ✅ (`component-contracts.md` §1) |
| SchemaCatalog contract | ✅ (`component-contracts.md` §2) |
| SchemaRetriever contract | ✅ (`component-contracts.md` §3) |
| QueryPlanner contract | ✅ (`component-contracts.md` §4) |
| SQLGenerator contract | ✅ (`component-contracts.md` §5) |
| SQLValidator/SecurityValidator boundary | ✅ (ADR-014, `component-contracts.md` §6, §7) |
| SQLRepairer contract | ✅ (`component-contracts.md` §15) |
| QueryExecutor contract | ✅ (`component-contracts.md` §10) |
| ResultAnalyzer contract | ✅ (`component-contracts.md` §11) |
| PresentationPlanner contract | ✅ (`component-contracts.md` §12) |
| AnswerGenerator contract | ✅ (`component-contracts.md` §13) |
| AnswerValidator contract | ✅ (`component-contracts.md` §14) |
| MemoryManager boundary | ✅ (`component-contracts.md` §16) |
| LLMRouter boundary | ✅ (`component-contracts.md` §17) |
| AgentState interaction defined | ✅ (`interface-design.md` §6) |
| Observability boundary defined | ✅ (`interface-design.md` §8) |
| Error contract strategy defined | ✅ (`interface-design.md` §7, ADR-015) |
| Side-effect matrix | ✅ (`component-contracts.md` §18) |
| Dependency matrix | ✅ (`component-contracts.md` §19) |
| Sync/async decisions | ✅ (`interface-design.md` §12) |
| Public API vs internal contract boundaries | ✅ (`interface-design.md` §10, §11) |
| Sprint 0/1 decisions preserved | ✅ (`interface-design.md` §13) |
| No implementation code added | ✅ (§18) |
| Documentation internally consistent | ✅ (all three contract documents cross-reference each other and the ADRs) |

**Definition of Done: PASS**

---

## Final Verification

- [x] No runtime code was intentionally modified
- [x] No Prompt was rewritten
- [x] No API behavior changed
- [x] No AgentState behavior changed
- [x] No Security behavior changed
- [x] No Database behavior changed
- [x] No Memory behavior changed
- [x] Documentation matches actual findings (each contract was derived
      from the code inspected in §2)
- [x] Target contracts are consistent with AgentState (state-applier
      methods follow the `with_*` pattern already in use)
- [x] Target contracts are consistent with Sprint 0/1 architecture
      (Security Core authoritative, single DB boundary, typed errors,
      request-scoped State — all preserved)
- [x] No future implementation was accidentally started (no Protocol
      classes, no ABCs, no interface modules, no new components)

If any runtime file was modified unexpectedly, it is reported here:
**None.**

---

## Recommendation for Prompt 2B

Begin the implementation phase per §15. The contracts in this prompt are
the authoritative design reference; Prompt 2B will build the plumbing
    (Orchestrator + typed value objects + state-applier methods + new
    tests) without breaking the existing 209 tests.

---

**Sprint 2 — Prompt 2A Complete**