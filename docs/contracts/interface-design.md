# Interface Design — Target Component Contracts

**Sprint**: 2 — Prompt 2A
**Status**: Authoritative design (no implementation in this prompt)
**Sources of truth**:
- Current implementation (`src/`) — current behavior
- Target architecture (`docs/architecture/target-architecture.md`, `docs/architecture/target-data-flow.md`, `docs/architecture/component-responsibilities.md`)
- Approved ADRs (`docs/adr/ADR-001..011`)
- AgentState contract (`docs/contracts/agent-state.md`)

---

## 1. Purpose

This document defines the **formal interfaces** between every approved target
component of the Text-to-SQL pipeline. Its purpose is to make the boundaries
between components explicit *before* any component is built, so future prompts
can implement each component against a stable contract rather than against
implicit expectations.

The contract is intentionally **narrow**: each interface states what enters,
what leaves, what errors can occur, what side effects may happen, what
dependencies are allowed, and what state is touched. Anything not stated here
is forbidden unless explicitly revised.

The interfaces in this document are the boundary between:
- **Application** code (orchestrator, components)
- **Infrastructure** (security, database, LLM, memory, observability)
- **Presentation** (API, CLI, Streamlit)

They are NOT the public HTTP API contract. The public API is owned by
`src/api/` and remains unchanged in this prompt (see §10 *Public API
Compatibility*).

---

## 2. Design Principles

The contracts in this document follow seven principles, derived from Sprint 0
and Sprint 1 decisions and the AgentState contract.

1. **Stable contracts, swappable implementations.** Components depend on
   interface contracts, not on concrete classes. A component is replaceable
   without rewriting callers, provided the new implementation honors the
   contract.

2. **State is owned by the orchestrator.** Components return typed results.
   The orchestrator applies those results to `AgentState`. Components do not
   mutate shared state directly. (See §9 *State Interaction Strategy*.)

3. **Security decisions are deterministic and fail closed.** The Security
   Core from Sprint 1 (ADR-004) is authoritative. No interface may allow a
   caller to bypass it or to retry past it without re-entering validation.

4. **One database boundary.** `QueryExecutor` is the only interface allowed
   to talk to SQL Server. (ADR-006)

5. **LLM is a typed service.** LLM calls go through `LLMRouter`, which owns
   provider, model, retries, budgets and observability. Domain components
   receive *text* and *metadata*; they do not know about providers. (ADR-011)

6. **Observability is cross-cutting.** Components do not own their own
   logging packages. They use `log_event()` and a `TraceContext` propagated
   via contextvar. (ADR-009/010 + Sprint 1 Prompt 4)

7. **Errors are typed and stage-tagged.** Every component raises errors from
   the typed hierarchy defined in §7 *Error Contract Strategy*. Raw exception
   text does not cross an interface boundary.

---

## 3. Layering

```
┌─────────────────────────────────────────────────────────────────────┐
│ Presentation                                                       │
│   /api/v1/ask, CLI (run.py), Streamlit (src/web/app.py)             │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Application / Orchestration                                         │
│   AgentOrchestrator (future, from prompt 2B)                        │
│   Component facades injected with stable contracts                 │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Domain Components (Application logic)                               │
│   QuestionAnalyzer, SchemaRetriever, QueryPlanner,                  │
│   SQLGenerator, SQLRepairer, ResultAnalyzer,                        │
│   PresentationPlanner, AnswerGenerator, AnswerValidator             │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Deterministic Validators (Domain rules, no LLM, no DB)              │
│   SQLParser, SQLValidator, SecurityValidator, SemanticValidator,     │
│   ComplexityValidator                                               │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Infrastructure Adapters                                             │
│   QueryExecutor, SchemaCatalog, MemoryManager,                      │
│   LLMRouter, TraceContext, StructuredLogger                         │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ External Systems                                                    │
│   SQL Server, LLM Provider(s), SQLite, File/Console logs            │
└─────────────────────────────────────────────────────────────────────┘
```

**Dependency direction**: each layer depends only on the layer immediately
below. Higher layers never depend on lower-layer concrete classes; they depend
on the contracts documented here.

---

## 4. Inventory of Target Components

The 15 target components documented below were selected from the target
architecture (`target-architecture.md`, `target-data-flow.md`) and the
approved ADRs. Each component is classified and given an interface even when
it already exists in some form today, so the future Prompt 2B implementation
can replace current behavior without ambiguity.

| # | Component | Layer | ADRs | Status Today |
|---|-----------|-------|------|--------------|
| 1 | QuestionAnalyzer | Domain (LLM-assisted) | ADR-001 | Missing |
| 2 | SchemaCatalog | Infrastructure | ADR-002 | Missing (static fallback) |
| 3 | SchemaRetriever | Domain | ADR-002 | Missing |
| 4 | QueryPlanner | Domain (LLM-assisted) | ADR-003 | Missing |
| 5 | SQLGenerator | Domain (LLM-assisted) | ADR-001 | Present as `SQLExtractor.extract()` |
| 6 | SQLValidator | Domain (deterministic) | ADR-004, ADR-005 | Present as `SQLValidator`/`SQLSecurityValidator` |
| 7 | SecurityValidator | Domain (deterministic) | ADR-004 | Present as `SQLSecurityValidator` |
| 8 | SemanticValidator | Domain (deterministic) | ADR-005 | Missing |
| 9 | SQLRepairer | Domain (LLM-assisted) | ADR-001 | Implicit in `SQLExtractor.extract()` retry loop |
| 10 | QueryExecutor | Infrastructure | ADR-006 | Present as `DatabaseConnection.execute_query()` |
| 11 | ResultAnalyzer | Domain (deterministic) | ADR-007 | Missing |
| 12 | PresentationPlanner | Domain (deterministic) | ADR-008 | Missing (static `COLUMN_MAP` partial) |
| 13 | AnswerGenerator | Domain (LLM-assisted) | ADR-001 | Present as inline LLM call in `TextToSQLAgent.ask()` |
| 14 | AnswerValidator | Domain (LLM-assisted) | ADR-005 | Missing |
| 15 | MemoryManager | Infrastructure | ADR-009 | Present as `src/memory/manager.py` |

Two additional infrastructure adapters are part of the contract set but not
domain components:

| # | Component | Layer | ADRs | Status Today |
|---|-----------|-------|------|--------------|
| 16 | LLMRouter | Infrastructure | ADR-011 | Missing (current: `LLMClient` facade) |
| 17 | Observability (`TraceContext`, `StructuredLogger`) | Cross-cutting | ADR-010 + Sprint 1 Prompt 4 | Present as `src/utils/logger.py` |

The full per-component contract is in
[`docs/contracts/component-contracts.md`](./component-contracts.md).
The dependency rules between components are in
[`docs/architecture/dependency-rules.md`](../architecture/dependency-rules.md).

---

## 5. Classification

Each component is classified along three orthogonal axes. The classification
informs future dependency tests and architecture tests.

### 5.1 Architectural layer

| Layer | Meaning | Members |
|-------|---------|---------|
| Presentation | Transport (HTTP, CLI, Streamlit) | API routes, CLI, Streamlit |
| Application | Orchestration and use cases | `AgentOrchestrator` (future) |
| Domain | Business rules; the text-to-SQL problem | Components 1, 3, 4, 5, 8, 9, 11, 12, 13, 14 |
| Validator | Deterministic rule enforcement | Components 6, 7 |
| Infrastructure | External-system adapters | Components 2, 10, 15, 16 |
| Cross-cutting | Available everywhere via context | Observability |
| Security | Security boundary policy | SecurityValidator (also counted as Validator) |
| External Adapter | Wraps vendor SDK | `OpenAIClient`, `GroqClient`, `GeminiClient`, `OllamaClient` (all live behind `LLMRouter`) |
| Evaluation | Offline harness | `EvaluationAgentAdapter`, `EvaluationRunner` |

### 5.2 Behavior

| Tag | Meaning | Components |
|-----|---------|------------|
| Deterministic | Pure function of inputs; no LLM, no network, no DB | SQLParser, SQLValidator, SecurityValidator, SemanticValidator, ComplexityValidator, SchemaCatalog, QueryExecutor, ResultAnalyzer, PresentationPlanner, AnswerValidator (deterministic checks), TraceContext, StructuredLogger |
| LLM-assisted | Calls an LLM through `LLMRouter` | QuestionAnalyzer, QueryPlanner, SQLGenerator, SQLRepairer, AnswerGenerator |
| External service | Talks to a vendor SDK or remote service | LLM providers (behind `LLMRouter`), MemoryManager (SQLite), QueryExecutor (SQL Server) |
| Persistence-backed | Reads/writes durable storage | MemoryManager, SchemaCatalog (snapshot cache) |
| Security-sensitive | A failure of correctness breaks the security boundary | SecurityValidator, QueryExecutor, SQLParser (must not accept multi-statements) |

### 5.3 Side effects

Side effects are summarized in the **Side-effect Matrix** in
[`docs/contracts/component-contracts.md`](./component-contracts.md#9-side-effect-matrix).
The rule is: a component whose side effect table does not list "DB Read" or
"DB Write" must not perform that side effect, and any implementation that
does so fails the architecture test.

---

## 6. State Interaction Strategy

The component contract is the **mechanism** by which data flows between
components; `AgentState` is the **vehicle** that carries data between
pipeline stages.

### 6.1 The rule

> Components receive data they need as parameters. They return typed
> results. The orchestrator applies the result to `AgentState`. Components
> do not mutate `AgentState` directly.

This rule is the architectural separation between *behavior* (the
component) and *state* (the orchestrator). It prevents components from
becoming hidden God Objects that share mutable state.

### 6.2 Why this rule now

The current implementation already follows this rule *implicitly* because
`TextToSQLAgent.ask()` is the sole writer of `AgentState` (verified in
Sprint 2 Prompt 1C). The Prompt 2A contribution is to make this rule
**explicit and contractual**, so future components that are added (e.g.
`QuestionAnalyzer`, `QueryPlanner`) cannot silently mutate shared state.

### 6.3 Concrete pattern

A component call site in the orchestrator looks like:

```text
result: ComponentResult = analyzer.analyze(state.question_analysis_input)
state = state.with_question_analysis(result.spec)        # orchestrator writes
```

The component itself returns:

```text
class QuestionSpec:
    intent: str
    entities: list[Entity]
    filters: list[Filter]
    time_constraints: list[TimeConstraint]
    ambiguity: AmbiguityReport | None
    confidence: float
```

The component never imports `AgentState`. It does not import the
`Orchestrator`. It does not log to a file. It calls `log_event(...)` only if
it is performing a measurable action worth a structured event; otherwise it
returns silently.

### 6.4 State access matrix (read / write / return)

| Component | Reads AgentState | Writes AgentState | Returns Data for State |
|-----------|:----------------:|:-----------------:|:----------------------:|
| QuestionAnalyzer | `request.question`, `conversation.history` | no | `QuestionSpec` |
| SchemaCatalog | none (pure schema access) | no | `SchemaSnapshot` |
| SchemaRetriever | `QuestionSpec` (via parameter) | no | `SchemaContext` |
| QueryPlanner | `QuestionSpec`, `SchemaContext` (via params) | no | `QueryPlan` |
| SQLGenerator | `QueryPlan`, `SchemaContext` (via params) | no | `SQLCandidate` |
| SQLParser | none (raw SQL only) | no | parsed AST + diagnostics |
| SQLValidator | parsed AST, `SchemaSnapshot` (via params) | no | `ValidationReport` |
| SecurityValidator | raw SQL + `SecurityPolicy` | no | `SecurityDecision` |
| SemanticValidator | parsed AST, `QueryPlan`, `SchemaSnapshot` | no | `SemanticReport` |
| ComplexityValidator | parsed AST | no | `ComplexityReport` |
| SQLRepairer | `SQLCandidate`, `ValidationReport`, `SemanticReport`, `QueryPlan` | no | new `SQLCandidate` |
| QueryExecutor | raw SQL + `ExecutionPolicy` | no | `QueryResult` |
| ResultAnalyzer | `QueryResult`, `QueryPlan` | no | `ResultProfile` |
| PresentationPlanner | `QuestionSpec`, `ResultProfile`, locale policy | no | `PresentationPlan` |
| AnswerGenerator | `QuestionSpec`, `ResultProfile`, `PresentationPlan`, rows | no | `AnswerDraft` |
| AnswerValidator | `AnswerDraft`, `ResultProfile` | no | `ValidatedAnswer` |
| MemoryManager | none (own session storage) | yes (its own table) | turns/history |
| LLMRouter | none (stateless router) | no | text + usage metadata |
| Orchestrator | **yes (sole writer)** | **yes (sole writer)** | n/a |

The "Orchestrator" row above is the only row that writes `AgentState`. This
is the rule that the dependency matrix in §5 of
`dependency-rules.md` enforces.

---

## 7. Error Contract Strategy

### 7.1 Goals

1. **Stable, machine-readable codes.** Each error has a single, versioned
   code that never changes meaning once published.
2. **Stage-tagged errors.** The error tells the orchestrator which pipeline
   stage produced it, so retries and UI messaging are correct.
3. **No raw exception text crossing boundaries.** Internal exception messages
   are mapped to typed `code` + sanitized `user_message`.
4. **Backwards compatibility with existing handlers.** Existing handlers
   (`SQLValidationError`, `SQLSecurityError`, `QueryExecutionError`,
   `DatabaseError`) remain catchable where they exist today.

### 7.2 Typed hierarchy

The error hierarchy is **additive only** (no breaking changes):

```text
TextToSQLException (base, src/utils/exceptions.py)
├── ConfigurationError                  (existing)
├── SQLValidationError                  (existing, kept)
│   └── ParserError                     (new, semantic-only)
├── SQLSecurityError                    (existing, from sql_security)
├── QueryExecutionError                  (existing)
│   └── DatabaseError                   (existing, with DatabaseErrorCode)
├── LLMError                            (existing)
├── QuestionAnalysisError               (new)
├── SchemaError                         (new)
├── PlanningError                       (new)
├── SQLGenerationError                  (new)
├── RepairError                         (new)
├── ResultAnalysisError                 (new)
├── PresentationError                   (new)
├── AnswerGenerationError               (new)
├── AnswerValidationError               (new)
├── MemoryError                         (new, replaces bare sqlite3)
└── LLMRouterError                      (new, replaces bare RuntimeError)
```

### 7.3 Common shape

Every typed error carries:

| Field | Type | Purpose |
|-------|------|---------|
| `code` | `str` | Stable machine-readable code (see §7.4) |
| `message` | `str` | Internal-only message; not shown to user |
| `user_message` | `str` | Safe UI text (no internal paths, no SQL) |
| `stage` | `PipelineStage` | Where in the pipeline the error happened |
| `category` | `ErrorCategory` | Coarse bucket (see §7.5) |
| `retryable` | `bool` | Whether the orchestrator may attempt a retry |
| `cause` | `Exception \| None` | Underlying exception (not serialized across boundaries) |

### 7.4 Stable code catalog (v1)

Codes are namespaced with the component prefix, lowercase, snake_case.

| Prefix | Component | Example codes |
|--------|-----------|---------------|
| `QA.` | QuestionAnalyzer | `QA.AMBIGUOUS`, `QA.UNSUPPORTED_INTENT`, `QA.LLM_UNAVAILABLE` |
| `SCH.` | SchemaCatalog / SchemaRetriever | `SCH.DRIFT`, `SCH.NO_RELEVANT_SCHEMA`, `SCH.UNAVAILABLE`, `SCH.CONTEXT_BOUNDED` (Phase 3.2 context warning: retrieval trimmed the context to its budget; also reports structural-floor breaches) |
| `PLN.` | QueryPlanner | `PLN.PLAN_CONFLICT`, `PLN.AMBIGUOUS`, `PLN.LLM_UNAVAILABLE` |
| `GEN.` | SQLGenerator | `GEN.LLM_UNAVAILABLE`, `GEN.INVALID_OUTPUT` |
| `PAR.` | SQLParser | `PAR.SYNTAX_ERROR`, `PAR.MULTIPLE_STATEMENTS` |
| `VAL.` | SQLValidator | `VAL.SYNTAX`, `VAL.SCHEMA_REFERENCE` |
| `SEC.` | SecurityValidator | `SEC.MULTIPLE_STATEMENTS`, `SEC.NON_READ_ONLY`, `SEC.FORBIDDEN_DML/DDL/EXEC/TRANSACTION`, `SEC.SELECT_INTO`, `SEC.UNSUPPORTED_CONSTRUCT` |
| `SEM.` | SemanticValidator | `SEM.AGGREGATE_IN_WHERE`, `SEM.UNIT_MISMATCH`, `SEM.CURRENCY_MISMATCH`, `SEM.JOIN_MISSING`, `SEM.FILTER_AMBIGUOUS` |
| `CMX.` | ComplexityValidator | `CMX.TOO_COMPLEX`, `CMX.COST_OVER_BUDGET` |
| `RPR.` | SQLRepairer | `RPR.EXHAUSTED`, `RPR.NO_RECOVERY` |
| `EXE.` | QueryExecutor | `EXE.TIMEOUT`, `EXE.ROW_LIMIT_EXCEEDED`, plus all existing `DATABASE_*` codes (kept verbatim) |
| `RES.` | ResultAnalyzer | `RES.INTERNAL_ERROR` |
| `PRE.` | PresentationPlanner | `PRE.POLICY_VIOLATION` |
| `ANS.` | AnswerGenerator | `ANS.LLM_UNAVAILABLE`, `ANS.OUTPUT_TOO_LONG` |
| `ANV.` | AnswerValidator | `ANV.UNGROUNDED_CLAIM`, `ANV.NUMERIC_MISMATCH`, `ANV.FORMAT_VIOLATION` |
| `MEM.` | MemoryManager | `MEM.UNAVAILABLE`, `MEM.SESSION_LOST` |
| `ROU.` | LLMRouter | `ROU.PROVIDER_UNAVAILABLE`, `ROU.BUDGET_EXCEEDED`, `ROU.NO_PROVIDER` |
| `CFG.` | Configuration | `CFG.MISSING`, `CFG.INVALID` |

### 7.5 Error category (coarse bucket)

The existing `ErrorCategory` enum in `src/agents/state.py` is preserved and
extended. New values are added without removing existing ones.

```text
QUESTION | SCHEMA | PLANNING | SQL_GENERATION | SECURITY
VALIDATION | EXECUTION | RESPONSE | ANSWER_VALIDATION | MEMORY
LLM | INTERNAL
```

### 7.6 Error stage tags (`ErrorStageTag`)

The `stage` field is carried as an `ErrorStageTag` (see
`src/agents/contracts.py`). Every stage tag value is listed here so the
registry stays documented and complete:

| `ErrorStageTag` | Pipeline stage | Raised by |
|-----------------|----------------|-----------|
| `QUESTION` | question understanding | QuestionAnalyzer |
| `SCHEMA` | schema resolution | SchemaCatalog / SchemaRetriever |
| `PLANNING` | query planning | QueryPlanner |
| `SQL_GENERATION` | SQL generation | SQLGenerator |
| `SECURITY` | security validation | SecurityValidator |
| `VALIDATION` | syntactic validation | SQLValidator |
| `SEMANTIC` | semantic validation | SemanticValidator |
| `COMPLEXITY` | complexity validation | ComplexityValidator |
| `REPAIR` | SQL repair | SQLRepairer |
| `EXECUTION` | database execution | QueryExecutor |
| `RESULT` | result analysis | ResultAnalyzer |
| `PRESENTATION` | presentation planning | PresentationPlanner |
| `ANSWER` | answer generation | AnswerGenerator |
| `ANSWER_VALIDATION` | answer validation | AnswerValidator |
| `MEMORY` | memory operations | MemoryManager |
| `LLM` | LLM routing / calls | LLMRouter |
| `INTERNAL` | anything uncategorised | internal fallback |

### 7.7 Retry policy

| Category | Default retryable? |
|----------|:------------------:|
| `LLM` | yes (with budget) |
| `EXECUTION` | depends on sub-code: TIMEOUT/TRANSIENT yes, RESULT_LIMIT no |
| `VALIDATION` | no (deterministic) |
| `SECURITY` | no (deterministic, fail-closed) |
| `QUESTION` (ambiguous) | yes (orchestrator may ask user) |
| everything else | no |

Retry policy is enforced by the orchestrator; components themselves do not
retry. `SQLRepairer` is a controlled, bounded retry (see its contract).

---

## 8. Observability Boundary

Observability is **cross-cutting infrastructure**. Components do not own a
logger instance; they call `log_event(...)` from `src/utils/logger.py` and
rely on `TraceContext` being set by the entry boundary.

### 8.1 Components do not import

- `logging` (stdlib) directly — they go through `get_logger` / `log_event`.
- `json` — `JsonFormatter` owns serialization.
- `os.path` for log files — `configure_logging` owns that.

### 8.2 Trace propagation

- Entry boundary (FastAPI middleware / CLI / Evaluation adapter) sets
  `TraceContext` via `set_trace_context(trace_id, session_id)`.
- Each request-scoped component method receives no explicit trace argument;
  it reads `get_trace_context()`.
- `TraceContext` uses `contextvars`, which is task-local in asyncio and
  thread-local in threads.
- The orchestrator ensures that, after a request finishes, the trace context
  is reset (`reset_trace_context(token)`) — this is preserved from Sprint 1.

### 8.3 Required instrumentation per component

Each component emits at least two lifecycle events:

- `<component>_started` (INFO) — with non-sensitive inputs only.
- `<component>_completed` (INFO) — with outcome summary.
- `<component>_failed` (WARNING/ERROR) — on typed error.

LLM-using components also emit `llm_call_started` / `llm_call_completed` /
`llm_call_failed` so the LLMRouter can correlate.

### 8.4 No `print()` in components

The current code emits `print(...)` from `src/web/app.py` (Streamlit) and
`run.py` (CLI). Those are *adapters*, not components. **Components never
`print`.**

---

## 9. Database Boundary (QueryExecutor)

The database boundary is single and explicit. From ADR-006:

> `QueryExecutor` is the only DB boundary and enforces read-only credentials,
> timeout, row/column caps, and request isolation.

The contract in
[`docs/contracts/component-contracts.md`](./component-contracts.md#10-queryexecutor)
states that:

- `QueryExecutor.execute(sql, *, params, budget)` is the only public method.
- `QueryExecutor` does NOT validate SQL security itself; it assumes the
  caller has either passed already-validated SQL or invokes `SecurityValidator`
  via a documented hook before calling `execute`. The current `DatabaseConnection.execute_query`
  already performs the security check internally (Sprint 1); the contract
  preserves this as a documented integration **before-execute**, not as a
  contractual responsibility of `QueryExecutor`.
- Typed `DatabaseError` with stable `DatabaseErrorCode` is preserved.
- `execute_non_query()` is documented to fail-closed.

---

## 10. Public API Compatibility

The public HTTP API contract at `POST /api/v1/ask` is owned by
`src/api/routes.py` and is **not part of the internal component contracts**.

Internal contracts may change freely between prompts as long as:
- `AskRequest` (Pydantic model) is unchanged.
- `AskResponse` (Pydantic model) is unchanged.
- The success/failure response dict returned by
  `state_to_api_response(state)` matches the current format
  (verified in Sprint 1 + Sprint 2 Prompt 1B/1C; 209 tests pass).

The CLI (`run.py`) and the Streamlit UI (`src/web/app.py`) consume the same
response dict. They are also not internal contracts.

The Evaluation adapter (`EvaluationAgentAdapter`) consumes the same response
dict and is **not** a target component in the new pipeline. It is part of
the test harness and remains frozen in this prompt.

---

## 11. Contract Versioning

### 11.1 Internal contracts

Internal component contracts (this document and `component-contracts.md`)
are versioned implicitly by adding new fields and new components. They
are NOT versioned explicitly (no `v1`, `v2`, `v3`); instead, additive
changes are accepted and breaking changes require an ADR.

### 11.2 Public API

The `/api/v1/ask` contract is versioned through the `/api/v1/` prefix and
is **frozen** at v1 within this prompt. New public endpoints (when needed)
appear under `/api/v2/`. The current `/api/v1/ask` schema is:

```jsonc
// Request
{ "question": "...", "session_id": "..." }   // session_id optional

// Response (success)
{ "success": true, "question": "...", "answer": "...",
  "sql": "...", "row_count": 42, "rows": [...],
  "session_id": "..." }

// Response (failure)
{ "success": false, "question": "...", "error": "...",
  "sql": null, "session_id": "...",
  "database_error_code": "..." }             // optional
```

### 11.3 Persistence

MemoryManager persistence (`data/conversations.db` schema) is a private
contract. Changes require migration but do not affect API or component
contracts.

---

## 12. Sync / Async

| Component | Sync / Async | Rationale |
|-----------|--------------|-----------|
| All Deterministic Validators | sync | CPU-only, no I/O |
| SchemaCatalog | sync | In-memory snapshot with cached disk read |
| SchemaRetriever | sync | Reads in-memory snapshot |
| QueryPlanner | sync from the orchestrator's view; internal LLM call returns text | LLM call is blocking in current providers |
| SQLGenerator | sync (LLM call is blocking) | Same as QueryPlanner |
| SQLRepairer | sync (LLM call is blocking) | Same |
| AnswerGenerator | sync (LLM call is blocking) | Same |
| AnswerValidator | sync | Pure function over inputs |
| ResultAnalyzer | sync | Pure function over inputs |
| PresentationPlanner | sync | Pure function over inputs |
| QueryExecutor | sync (driver call is blocking) | pymssql is blocking; no async driver in current deps |
| MemoryManager | sync | SQLite + per-operation connection |
| LLMRouter | sync (delegates to blocking provider SDKs) | OpenAI/Gemini SDKs are sync; future router may expose async façade |
| TraceContext | sync | contextvar operations are sync |
| FastAPI route handler | async (event loop friendly) | Wraps blocking agent call; acceptable per Sprint 0 decision |

**Decision**: components are **sync**. The orchestrator may expose an async
surface to the API by running the sync pipeline in a thread pool if needed.
Justification: the current LLM providers and DB driver are blocking; an async
component interface would require an async LLM and async driver, which is
out of scope for Prompt 2B.

---

## 13. Compatibility with Sprint 0 / Sprint 1 Decisions

This contract set **does not weaken** any Sprint 0 / Sprint 1 decision:

| Decision | Where preserved in contracts |
|----------|------------------------------|
| Security Core is authoritative (ADR-004) | SecurityValidator §7; cannot be bypassed; QueryExecutor integration rule §9 |
| AST-based fail-closed policy (Sprint 1 Prompt 2) | `SEC.*` codes preserved verbatim in §7.4 |
| Read-only QueryExecutor (ADR-006) | QueryExecutor §10 in `component-contracts.md` |
| Typed DB errors (Sprint 1 Prompt 3) | `EXE.*` and existing `DATABASE_*` codes preserved §7.4 |
| Row/time caps fail, not truncate (Sprint 1 Prompt 3) | QueryExecutor §10 (contract inherited) |
| Request-scoped fresh connection per query (Sprint 1 Prompt 3) | QueryExecutor §10 |
| TraceContext for all requests (Sprint 1 Prompt 4) | Observability §8 |
| Structured JSON logs with redaction (Sprint 1 Prompt 4) | Observability §8 |
| Evaluation adapter frozen (Sprint 1 Prompt 1) | §10 |
| `/api/v1/ask` v1 frozen (Sprint 1 + Prompt 1B/1C) | §11.2 |
| AgentState is request-scoped (Sprint 2 Prompt 1A/1B/1C) | §6 |

---

## 14. Contract Set Boundaries (Summary)

| Boundary | Owner | Enforced by |
|----------|-------|-------------|
| Agent ↔ Component | Contracts in `component-contracts.md` | Future architecture tests in Prompt 2B+ |
| Component ↔ Validator | Validator inputs documented per validator | Manual review; future tests |
| Component ↔ LLMRouter | `LLMRouter.generate(...)` signature | Manual review |
| Validator ↔ DB / Network | None (deterministic) | Architecture test: validators must not import DB or LLM SDKs |
| QueryExecutor ↔ DB | Single, documented | Sprint 1 Prompt 3 tests (already 18) |
| Orchestrator ↔ AgentState | Only the orchestrator writes | Sprint 2 Prompt 1C tests |
| TraceContext ↔ AgentState | One-way reference at creation | Sprint 2 Prompt 1C tests |
| MemoryManager ↔ AgentState | Orchestrator mediates | Sprint 2 Prompt 1C tests |
| API ↔ Internal | `state_to_api_response` is the seam | All 209 Sprint 1+Sprint 2 tests |

---

## 15. Open Design Questions (deferred to future prompts)

The following questions were considered and are **deferred** rather than
resolved here, because resolving them would force implementation decisions
outside the scope of contract design.

| # | Question | Deferred to |
|---|----------|-------------|
| Q1 | Should SQLGenerator and SQLRepairer be the same component or two? | Prompt 2B / Sprint 4 (when generation is implemented) |
| Q2 | Should SchemaCatalog be a single interface or split read/write? | Sprint 5 (Schema) |
| Q3 | Should MemoryManager have a typed turn model now or later? | Sprint 11 (Memory) |
| Q4 | Should LLMRouter expose async API? | Sprint 6 (LLM routing) |
| Q5 | Should AnswerValidator be deterministic-only or hybrid (LLM judge)? | Sprint 8 (Answer) |
| Q6 | Should ComplexityValidator be a separate component or part of SecurityValidator? | Sprint 4 (SQL pipeline) |
| Q7 | Should `CorrectionAttempt` be tracked in `SQLState` or `RuntimeMetadata`? | Sprint 4 (already in `SQLState.correction_attempt`) |

---

## 16. Verification Checklist

- [x] Current component boundaries audited (Step 2A.1)
- [x] Responsibility overlaps identified (Step 2A.2)
- [x] Missing target boundaries identified (Step 2A.3)
- [x] Target components classified (Step 2A.4)
- [x] Responsibilities and non-responsibilities documented (Step 2A.5)
- [x] Allowed/forbidden dependencies documented (Step 2A.6)
- [x] Interface direction documented (Step 2A.7)
- [x] QuestionAnalyzer contract (Step 2A.8)
- [x] SchemaCatalog / SchemaRetriever contracts (Step 2A.9)
- [x] QueryPlanner contract (Step 2A.10)
- [x] SQLGenerator contract (Step 2A.11)
- [x] SQLValidator / SecurityValidator boundary (Step 2A.12)
- [x] SQLRepairer contract (Step 2A.13)
- [x] QueryExecutor contract (Step 2A.14)
- [x] ResultAnalyzer contract (Step 2A.15)
- [x] PresentationPlanner contract (Step 2A.16)
- [x] AnswerGenerator contract (Step 2A.17)
- [x] AnswerValidator contract (Step 2A.18)
- [x] MemoryManager boundary (Step 2A.19)
- [x] LLMRouter boundary (Step 2A.20)
- [x] AgentState interaction defined (Step 2A.21)
- [x] Observability boundary defined (Step 2A.22)
- [x] Error contract strategy defined (Step 2A.23)
- [x] Side-effect matrix (Step 2A.24)
- [x] Dependency matrix (Step 2A.25)
- [x] Contract versioning rules (Step 2A.26)
- [x] Sync/async decisions (Step 2A.27)
- [x] Contract completeness review (Step 2A.28)
- [x] Documentation created (Step 2A.29)
- [x] ADRs created where decisions are architectural (Step 2A.30)
- [x] No implementation code added (Step 2A.31)