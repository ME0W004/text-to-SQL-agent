# Component Contracts

**Sprint**: 2 — Prompt 2A
**Status**: Authoritative contract set (no implementation in this prompt)
**Companion documents**:
- [`docs/contracts/interface-design.md`](./interface-design.md) — boundary, layering, classification, state and error strategies.
- [`docs/contracts/agent-state.md`](./agent-state.md) — `AgentState` schema and lifecycle.
- [`docs/architecture/dependency-rules.md`](../architecture/dependency-rules.md) — allowed/forbidden dependencies between components.

This document defines one **contract** per target component. Each contract
states: Purpose, Input, Output, Errors, Side Effects, Dependencies,
Forbidden Dependencies, State Interaction, Deterministic / LLM, and
Security Sensitivity.

For readability the contracts are presented as Markdown sections. Where the
Prompt 2A requirements list specific inputs/outputs (e.g. QuestionAnalyzer),
those are taken verbatim from the requirements and matched against the
target architecture before being accepted.

---

## Table of Contents

1. [QuestionAnalyzer](#1-questionanalyzer)
2. [SchemaCatalog](#2-schemacatalog)
3. [SchemaRetriever](#3-schemaretriever)
4. [QueryPlanner](#4-queryplanner)
5. [SQLGenerator](#5-sqlgenerator)
6. [SQLValidator](#6-sqlvalidator)
7. [SecurityValidator](#7-securityvalidator)
8. [SemanticValidator](#8-semanticvalidator)
9. [ComplexityValidator](#9-complexityvalidator)
10. [QueryExecutor](#10-queryexecutor)
11. [ResultAnalyzer](#11-resultanalyzer)
12. [PresentationPlanner](#12-presentationplanner)
13. [AnswerGenerator](#13-answergenerator)
14. [AnswerValidator](#14-answervalidator)
15. [SQLRepairer](#15-sqlrepairer)
16. [MemoryManager](#16-memorymanager)
17. [LLMRouter](#17-llmrouter)
18. [Side-effect Matrix](#18-side-effect-matrix)
19. [Dependency Matrix](#19-dependency-matrix)
20. [Contract Completeness Review](#20-contract-completeness-review)

---

## 1. QuestionAnalyzer

**Layer**: Domain (LLM-assisted).
**ADR**: ADR-001.
**Stage after which it is consumed**: `INITIALIZED` → `QUESTION_ANALYZED`.

### Purpose

Convert a user question (plus conversation context and any previously
resolved references) into a typed `QuestionSpec` describing intent, entities,
filters, time constraints, ambiguity and confidence. Does NOT generate SQL.
Does NOT touch the database.

### Input

| Parameter | Type | Source | Required |
|-----------|------|--------|:--------:|
| `question` | `str` | `RequestContext.question` | yes |
| `conversation` | `ConversationContext` | `AgentState.conversation` (immutable snapshot) | yes |
| `active_filters` | `dict` | from `ConversationContext.active_filters` | no |
| `resolved_references` | `dict` | from `ConversationContext.resolved_references` | no |

`QuestionAnalyzer` receives no mutable state, no AgentState, no DB
connection, no LLM SDK reference.

### Output

`QuestionSpec` (frozen dataclass):

```text
intent: str                                       # e.g. "aggregate", "lookup", "rank", "trend"
entities: tuple[Entity, ...]                      # mention spans with type
filters: tuple[Filter, ...]                       # equality, range, like, in
metrics: tuple[Metric, ...]                       # requested aggregations
time_constraints: tuple[TimeConstraint, ...]      # absolute / relative / Persian year
ambiguity: AmbiguityReport | None                 # unresolved references, missing info
confidence: float                                 # 0..1
references: tuple[Reference, ...]                 # cross-question follow-ups
warnings: tuple[str, ...]                         # non-fatal notes
```

Where `Entity`, `Filter`, `Metric`, `TimeConstraint`, `Reference` and
`AmbiguityReport` are frozen dataclasses defined in
`docs/contracts/types.md` (future file; types documented here conceptually).

### Errors

- `QuestionAnalysisError` with code:
  - `QA.AMBIGUOUS` — question cannot be resolved.
  - `QA.UNSUPPORTED_INTENT` — intent outside the supported set.
  - `QA.LLM_UNAVAILABLE` — `LLMRouter` returned no result.
  - `QA.INTERNAL` — other internal failure.

`QA.AMBIGUOUS` and `QA.UNSUPPORTED_INTENT` produce a `QuestionSpec` with a
populated `ambiguity` field; they are NOT raised as exceptions unless the
orchestrator policy requires it. The current behavior of returning a spec
even when ambiguous is preserved so the orchestrator can decide between
clarifying the user and rejecting the request.

### Side Effects

- One call to `LLMRouter.generate(task="question_analysis", ...)` (LLM).
- Two log events: `question_analysis_started`, `question_analysis_completed`.
- No DB, no file, no memory writes.

### Dependencies (may use)

- `LLMRouter` (via the contract defined in §17).
- `ConversationContext` (typed value object from AgentState).
- `log_event` and `TraceContext` (observability infrastructure).

### Forbidden Dependencies

- `SQLGenerator`, `QueryPlanner`, `QueryExecutor` — no forward references.
- `pymssql`, `sqlite3` — no DB.
- `OpenAIClient`, `GeminiClient`, `GroqClient`, `OllamaClient` directly — only
  `LLMRouter`.
- `AgentState` — components do not import it.
- `MemoryManager` — components do not import it.
- `src/api/*` — no API types.

### State Interaction

- **Reads**: `conversation.history`, `conversation.resolved_references`,
  `conversation.active_filters` — via parameters, not via import.
- **Writes**: none.
- **Returns**: `QuestionSpec` for the orchestrator to apply to
  `AgentState.question_analysis`.

### Deterministic / LLM

LLM-assisted. The exact model is chosen by `LLMRouter` based on
`task="question_analysis"`.

### Security Sensitivity

None directly. Output is typed and validated downstream by
`SemanticValidator` and `AnswerValidator`. Output never contains user
PII in clear form (entities are typed, not raw text).

---

## 2. SchemaCatalog

**Layer**: Infrastructure.
**ADR**: ADR-002.
**Status today**: missing; current code has static `FULL_SCHEMA` in
`src/core/database/models.py` (drift risk: Shipments omitted, per
`codebase-audit.md`).

### Purpose

Own the live, versioned schema metadata for the database. Provide
snapshots. Be the single source of truth for schema information.

### Input

| Method | Input | Output |
|---------|-------|--------|
| `get_snapshot(version: str | None = None)` | optional version pin | `SchemaSnapshot` |
| `current_version()` | none | version identifier (`str`) |
| `versions()` | none | `tuple[SchemaVersion, ...]` |
| `refresh()` | none (admin) | new version + cache update |

### Output: `SchemaSnapshot`

```text
version: SchemaVersion                          # (id, captured_at, source)
tables: tuple[TableDescriptor, ...]             # name, columns, primary_key, foreign_keys
relationships: tuple[Relationship, ...]        # parsed FK + named relationships
business_semantics: tuple[BusinessSemantics, ...]  # e.g. revenue = quantity * unitPrice
labels: dict[str, str]                          # column → display label (replaces static COLUMN_MAP)
```

`SchemaSnapshot` is **immutable**. New versions are produced by
`refresh()`; consumers always read snapshots.

### Errors

- `SchemaError` with code:
  - `SCH.UNAVAILABLE` — no live connection to introspect.
  - `SCH.DRIFT` — schema hash changed since last snapshot.
  - `SCH.VERSION_NOT_FOUND` — requested version missing.

### Side Effects

- One DB read per `refresh()` (introspection query against
  `INFORMATION_SCHEMA`).
- One file-system write per `refresh()` to the local snapshot cache.
- Log events: `schema_refresh_started`, `schema_refresh_completed`,
  `schema_refresh_failed`.

### Dependencies

- Database introspection (read-only connection — separate from the
  per-request `QueryExecutor` connection).
- File system cache.
- `log_event`.

### Forbidden Dependencies

- `LLMRouter` — catalog is deterministic.
- `QueryExecutor` — the per-request executor must not call the catalog.
  The catalog is its own snapshot-fetch path with its own (admin-time)
  connection.
- `MemoryManager`, `AgentState`.

### State Interaction

None. The orchestrator passes the snapshot into `SchemaRetriever`.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

Medium. Schema information disclosure is acceptable in this product
(authorized users). However the catalog must NEVER execute user SQL
during introspection.

---

## 3. SchemaRetriever

**Layer**: Domain.
**ADR**: ADR-002.

### Purpose

Given a `QuestionSpec` and a `SchemaSnapshot`, return a `SchemaContext`:
the subset of tables, columns and relationships the planner is allowed to
use, with any business semantics that apply.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `spec` | `QuestionSpec` | from `QuestionAnalyzer` |
| `snapshot` | `SchemaSnapshot` | from `SchemaCatalog` |

### Output: `SchemaContext`

```text
tables: tuple[TableRef, ...]                    # qualified table names
columns: tuple[ColumnRef, ...]                  # qualified column names
joins: tuple[JoinHint, ...]                      # FK-derived join paths
relevant_business_semantics: tuple[BusinessSemantics, ...]
confidence: float
warnings: tuple[str, ...]
```

`SchemaContext` is **immutable**.

### Errors

- `SchemaError` with code:
  - `SCH.NO_RELEVANT_SCHEMA` — spec could not be mapped to any table.
  - `SCH.AMBIGUOUS` — multiple equally likely candidates.
  - `SCH.CONTEXT_BOUNDED` (Phase 3.2) — a retrieval limit trimmed the
    context to its budget, or the structural floor exceeded it (the
    breach is reported, never hidden).

These errors produce a `SchemaContext` with `warnings` populated, and
are raised only if the orchestrator policy requires hard rejection.

### Side Effects

- None (no DB, no LLM).
- One log event: `schema_retrieval_completed` (Phase 3.2 payload:
  outcome, selected counts, `context_chars`, confidence, `bounded`
  flag, snapshot version, warning codes, duration — never the question,
  prompts, schema text or row data).

### Dependencies

- `SchemaSnapshot` (typed value object).

### Forbidden Dependencies

- `LLMRouter` — retrieval is deterministic.
- `QueryExecutor`.
- `SQLGenerator` — schema retrieval does not know about SQL syntax.

### Output bounds (Phase 3.2)

The returned `SchemaContext` is hard-bounded by `RetrievalPolicy`:
`table_top_k`, `column_top_k_per_table`, `max_join_hints`,
`max_semantic_entries` and `max_context_chars` (canonical serialized
size, measured by `serialized_context_size()`). Trimming follows a
documented deterministic priority (architecture doc §20.4); join
endpoints and warnings are never removed, and a structural-floor breach
is reported rather than hidden. The LLM-facing representation of the
context is rendered by `SchemaContextRenderer` (formatting only, hard
size limit, `src/core/schema/renderer.py`) — never by re-parsing the
snapshot.

### State Interaction

None. Returns `SchemaContext` to the orchestrator.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

Low. Operates only on metadata, not on data.

---

## 4. QueryPlanner

**Layer**: Domain (LLM-assisted).
**ADR**: ADR-003.
**Stage**: `SCHEMA_RESOLVED` → `PLAN_CREATED`.

### Purpose

Transform a `QuestionSpec` + `SchemaContext` into a typed `QueryPlan`:
tables, joins, dimensions, metrics, filters, grouping, sorting, limits,
and explicit output requirements.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `spec` | `QuestionSpec` | from `QuestionAnalyzer` |
| `context` | `SchemaContext` | from `SchemaRetriever` |

### Output: `QueryPlan`

```text
tables: tuple[TableRef, ...]
joins: tuple[Join, ...]                         # qualified + type
dimensions: tuple[Dimension, ...]                # group-by fields
metrics: tuple[Metric, ...]                     # aggregations with explicit type
filters: tuple[Filter, ...]                     # pre-aggregation filters
post_filters: tuple[Filter, ...]                # having-clause filters
sorting: tuple[SortKey, ...]
output_requirements: OutputRequirements
assumptions: tuple[str, ...]                    # explicit assumptions made by the planner
warnings: tuple[str, ...]
```

`QueryPlan` is **immutable**.

### Errors

- `PlanningError` with code:
  - `PLN.PLAN_CONFLICT` — competing metrics/filters incompatible.
  - `PLN.AMBIGUOUS` — planner could not decide.
  - `PLN.LLM_UNAVAILABLE`.

### Side Effects

- One `LLMRouter.generate(task="planning", ...)` call.
- Log events.

### Dependencies

- `LLMRouter`.
- `QuestionSpec`, `SchemaContext`.

### Forbidden Dependencies

- `SQLGenerator` — planner never writes SQL.
- `QueryExecutor` — planner never executes anything.
- `AgentState`.

### State Interaction

None. Returns `QueryPlan` to the orchestrator.

### Deterministic / LLM

LLM-assisted.

### Security Sensitivity

None directly. The planner is unaware of SQL.

---

## 5. SQLGenerator

**Layer**: Domain (LLM-assisted).
**ADR**: ADR-001.
**Stage**: `PLAN_CREATED` → `SQL_GENERATED`.

### Purpose

Given an approved `QueryPlan` + `SchemaContext`, emit a `SQLCandidate`:
a raw SQL string targeted at the configured dialect, plus metadata.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `plan` | `QueryPlan` | from `QueryPlanner` |
| `context` | `SchemaContext` | from `SchemaRetriever` |
| `dialect` | `str` | from configuration (default: `"tsql"`) |

### Output: `SQLCandidate`

```text
sql: str                                        # raw SQL text (untrusted at this stage)
dialect: str
candidate_id: str                               # uuid, for tracing repair loops
fingerprint: str                                # sha256 of normalized SQL
metadata: dict[str, Any]                        # generator-specific metadata
prompt_strategy: str                            # e.g. "few-shot-v1", "zero-shot"
```

`SQLCandidate` is **immutable**.

### Errors

- `SQLGenerationError` with code:
  - `GEN.LLM_UNAVAILABLE`
  - `GEN.INVALID_OUTPUT` — generator produced text that could not be parsed
    as SQL.
  - `GEN.BUDGET_EXCEEDED` — LLMRouter reports budget exhaustion.

### Side Effects

- One `LLMRouter.generate(task="sql_generation", ...)` call.
- Log events.

### Dependencies

- `LLMRouter`.
- `QueryPlan`, `SchemaContext`.

### Forbidden Dependencies

- `QueryExecutor` — generator does NOT execute SQL.
- `SecurityValidator` — generator does NOT validate SQL.
- `SQLValidator`, `SemanticValidator` — downstream validators must not be
  short-circuited.
- `AgentState`.
- Direct provider clients.

### State Interaction

None. Returns `SQLCandidate` to the orchestrator.

### Deterministic / LLM

LLM-assisted.

### Security Sensitivity

None directly. SQL is untrusted at this stage and MUST be validated by
`SecurityValidator` before any DB call.

---

## 6. SQLValidator

**Layer**: Domain (deterministic).
**ADR**: ADR-004 (security), ADR-005 (semantic — different component).
**Stage**: `SQL_GENERATED` → `VALIDATED` (after `SecurityValidator`).

### Purpose

Validate that the SQL is structurally well-formed and syntactically
correct for the declared dialect. Does NOT modify or execute SQL.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `candidate` | `SQLCandidate` | from `SQLGenerator` (or `SQLRepairer`) |
| `dialect` | `str` | from configuration |

### Output: `ValidationReport`

```text
candidate_id: str
valid: bool
parse_ok: bool
syntax_errors: tuple[SyntaxError, ...]
ast: ParsedAST | None                          # sqlglot expression, if parse_ok
warnings: tuple[str, ...]
```

`ValidationReport` is **immutable**.

### Errors

- `SQLValidationError` with code `VAL.SYNTAX` or `VAL.SCHEMA_REFERENCE`
  when `valid=False` and the orchestrator wants the failure surfaced as
  an exception rather than a `ValidationReport`.

### Side Effects

- Log event: `sql_validation_completed` with parse/syntax outcome.
- No DB, no LLM, no file.

### Dependencies

- `sqlglot` parser only.

### Forbidden Dependencies

- `LLMRouter`.
- `QueryExecutor`.
- `AgentState`.

### State Interaction

None. Returns `ValidationReport`.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

Low. SQLValidator confirms syntactic correctness only. It is NOT a
security boundary; `SecurityValidator` is.

---

## 7. SecurityValidator

**Layer**: Domain (deterministic) + Security.
**ADR**: ADR-004.
**Stage**: runs in the validation pass; failure transitions to `FAILED`.

### Purpose

Fail-closed AST-based decision on whether a SQL candidate may be executed:
exactly one read-only SELECT/WITH-SELECT statement, no DML/DDL/EXEC/
transaction/SELECT-INTO, no `OPENROWSET/OPENQUERY/OPENDATASOURCE/xp_*`.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `sql` | `str` | raw SQL text |
| `policy` | `SecurityPolicy` | configurable; defaults to `DEFAULT_SECURITY_POLICY` |

### Output: `SecurityDecision` (already exists in `src/core/security/sql_security.py`)

```text
valid: bool
reason_code: SecurityReasonCode | None
message: str
statement_type: str | None
statement_count: int
ast: exp.Expression | None                      # for downstream validators (not exposed externally)
```

### Errors

- `SQLSecurityError` with `code=SecurityReasonCode` — only raised when
  the orchestrator policy demands an exception.
- The default contract returns a `SecurityDecision` with `valid=False`.

### Side Effects

- Log event: `sql_security_decision` with reason_code.

### Dependencies

- `sqlglot` parser.
- `SecurityPolicy` (typed value object).

### Forbidden Dependencies

- `LLMRouter`.
- `QueryExecutor` — SecurityValidator does not run SQL.
- `AgentState`.

### State Interaction

None. Returns `SecurityDecision`.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

**Critical.** This is the authoritative security boundary. It must
remain deterministic, stateless, and free of bypass paths.

---

## 8. SemanticValidator

**Layer**: Domain (deterministic).
**ADR**: ADR-005.

### Purpose

Validate that the SQL candidate is semantically consistent with the
`QueryPlan` and the `SchemaSnapshot`. Checks joins, aggregates, filters,
units, currencies, business status (e.g. cancelled orders). Reports
violations; does NOT modify the SQL.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `candidate` | `SQLCandidate` | from generator or repairer |
| `plan` | `QueryPlan` | from planner |
| `snapshot` | `SchemaSnapshot` | from catalog |
| `ast` | `ParsedAST \| None` | from `SQLValidator` |

### Output: `SemanticReport`

```text
candidate_id: str
ok: bool
violations: tuple[SemanticViolation, ...]
warnings: tuple[str, ...]
```

`SemanticViolation` carries `code`, `message`, `severity`.

### Errors

- `SQLValidationError` with `code=SEM.*` only when `ok=False` and the
  orchestrator wants an exception.

### Side Effects

- Log event: `semantic_validation_completed`.

### Dependencies

- Parsed AST, `QueryPlan`, `SchemaSnapshot`.

### Forbidden Dependencies

- `LLMRouter`.
- `QueryExecutor`.
- `AgentState`.

### State Interaction

None.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

Low. Semantic violations are correctness issues, not security issues.

---

## 9. ComplexityValidator

**Layer**: Domain (deterministic).
**ADR**: ADR-004 follow-up; implied by `ExecutionPolicy`.

### Purpose

Estimate SQL complexity and reject candidates that exceed a configured
budget (joins, tables, depth, function call count, hint of cartesian
product). Reduces wasted DB round-trips.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `ast` | `ParsedAST` | from `SQLValidator` |
| `budget` | `ComplexityBudget` | configuration |

### Output: `ComplexityReport`

```text
ok: bool
score: float
violations: tuple[str, ...]
estimated_cost: str                            # "low" | "medium" | "high"
```

### Errors

- `SQLValidationError` with code `CMX.*`.

### Side Effects

- Log event only.

### Dependencies

- AST only.

### Forbidden Dependencies

- `LLMRouter`, `QueryExecutor`, `AgentState`.

### State Interaction

None.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

None.

---

## 10. QueryExecutor

**Layer**: Infrastructure.
**ADR**: ADR-006.
**Status today**: implemented as `DatabaseConnection.execute_query()`.

### Purpose

The **only** component allowed to execute SQL against SQL Server. Enforces
read-only policy, connection/cursor isolation per request, row/time caps
per `ExecutionPolicy`, typed error mapping.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `sql` | `str` | already security-approved SQL |
| `params` | `tuple \| dict \| None` | parameterized values |
| `policy` | `ExecutionPolicy` | configuration |

### Output: `QueryResult`

```text
columns: tuple[str, ...]                        # from cursor.description
rows: tuple[dict[str, Any], ...]
row_count: int
truncated: bool                                 # max_rows hit
duration_ms: float
executed_at: datetime
```

### Errors

`DatabaseError` (existing) with `DatabaseErrorCode`:

- `DATABASE_CONNECTION_ERROR`
- `DATABASE_TIMEOUT`
- `DATABASE_QUERY_ERROR`
- `DATABASE_PERMISSION_ERROR`
- `DATABASE_RESOURCE_LIMIT`
- `DATABASE_RESULT_LIMIT`
- `DATABASE_CURSOR_ERROR`
- `DATABASE_UNKNOWN_ERROR`

These names are preserved exactly; new `EXE.*` codes are aliases for the
existing set to maintain backward compatibility.

### Side Effects

- Opens a fresh DB connection (read-only session, per-request).
- Opens a fresh cursor (dictionary cursor).
- Closes both on every exit path.
- Log events: `db_execution_started`, `db_execution_completed`,
  `db_execution_failed`, `db_timeout`.

### Dependencies

- `pymssql` driver.
- `ExecutionPolicy` (typed value object).
- Security approval is a documented **integration** with `SecurityValidator`
  before `execute` is called; `QueryExecutor` itself does not re-validate
  (preserves Sprint 1 behavior).

### Forbidden Dependencies

- `LLMRouter`.
- `AgentState`.
- `MemoryManager`, `SchemaCatalog` — separate DB connections / paths.

### State Interaction

None. The orchestrator reads `QueryResult` and writes
`AgentState.execution`.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

**Critical.** Single database boundary. Must enforce read-only
credentials, timeouts, and row caps.

---

## 11. ResultAnalyzer

**Layer**: Domain (deterministic).
**ADR**: ADR-007.
**Stage**: `EXECUTED` → `RESULT_ANALYZED`.

### Purpose

Inspect a `QueryResult` and produce a typed `ResultProfile`: shape, type,
in
nulls, counts, truncation, aggregate provenance. Deterministic.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `result` | `QueryResult` | from `QueryExecutor` |
| `plan` | `QueryPlan` | from `QueryPlanner` |

### Output: `ResultProfile`

```text
shape: ShapeDescriptor                          # (rows, cols)
column_types: dict[str, ColumnType]             # inferred from values
null_counts: dict[str, int]
row_count: int
truncated: bool
aggregate_provenance: dict[str, AggregateRef]   # which metric produced which value
warnings: tuple[str, ...]
```

### Errors

- `ResultAnalysisError` with code `RES.INTERNAL`.

### Side Effects

- Log event only.

### Dependencies

- `QueryResult`, `QueryPlan` (typed value objects).

### Forbidden Dependencies

- `LLMRouter`, `QueryExecutor`, `AgentState`.

### State Interaction

None. Returns `ResultProfile`.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

None.

---

## 12. PresentationPlanner

**Layer**: Domain (deterministic).
**ADR**: ADR-008.
**Stage**: between `ResultAnalyzer` and `AnswerGenerator`.

### Purpose

Decide which columns to show, in what order, with which labels and
formats. Produces a `PresentationPlan`. Does not invent data; does not
perform business calculations not defined by the contract.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `spec` | `QuestionSpec` | from `QuestionAnalyzer` |
| `profile` | `ResultProfile` | from `ResultAnalyzer` |
| `labels` | `dict[str, str]` | from `SchemaSnapshot.labels` |
| `locale_policy` | `LocalePolicy` | configuration |

### Output: `PresentationPlan`

```text
visible_columns: tuple[str, ...]
column_order: tuple[str, ...]
display_labels: dict[str, str]
formats: dict[str, FormatSpec]                  # number, date, currency, unit
view_kind: str                                  # "table" | "kpi" | "ranking" | "timeseries"
notes: tuple[str, ...]
```

### Errors

- `PresentationError` with code `PRE.POLICY_VIOLATION`.

### Side Effects

- Log event only.

### Dependencies

- Typed value objects only.

### Forbidden Dependencies

- `LLMRouter`.
- `QueryExecutor` — planner never re-executes.
- `AnswerGenerator` — planner never writes the answer.
- `AgentState`.

### State Interaction

None.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

None.

---

## 13. AnswerGenerator

**Layer**: Domain (LLM-assisted).
**ADR**: ADR-001.
**Stage**: between `PresentationPlanner` and `AnswerValidator`.

### Purpose

Generate a natural-language answer grounded in the actual
`QueryResult` and `PresentationPlan`. Never invents data; never queries
the DB.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `spec` | `QuestionSpec` | from `QuestionAnalyzer` |
| `profile` | `ResultProfile` | from `ResultAnalyzer` |
| `presentation` | `PresentationPlan` | from `PresentationPlanner` |
| `result` | `QueryResult` | from `QueryExecutor` |
| `evidence` | `tuple[Evidence, ...]` | auxiliary context (memory recall, etc.) |

### Output: `AnswerDraft`

```text
text: str                                       # generated natural-language answer
format: str                                     # "markdown-persian" | "plain" | ...
claims: tuple[Claim, ...]                       # asserted facts (numeric or textual)
citations: tuple[str, ...]                      # source attributions (e.g. "row:3 col:total_revenue")
```

### Errors

- `AnswerGenerationError` with code:
  - `ANS.LLM_UNAVAILABLE`
  - `ANS.OUTPUT_TOO_LONG`
  - `ANS.INTERNAL`

### Side Effects

- One `LLMRouter.generate(task="answer_generation", ...)` call.
- Log events.

### Dependencies

- `LLMRouter`.
- Typed value objects.

### Forbidden Dependencies

- `QueryExecutor` — generator does NOT query the DB.
- `SecurityValidator`, `SQLGenerator`, `SQLRepairer` — generator does not
  know SQL.
- `MemoryManager` writes — generator does not persist directly.
- `AgentState`.

### State Interaction

None. Returns `AnswerDraft`.

### Deterministic / LLM

LLM-assisted.

### Security Sensitivity

Low. Output is validated downstream by `AnswerValidator`.

---

## 14. AnswerValidator

**Layer**: Domain (deterministic, optionally LLM-assisted).
**ADR**: ADR-005.
**Stage**: final domain step before `ResponseState` is written.

### Purpose

Validate that every numeric claim in the `AnswerDraft` is supported by
the `ResultProfile` and that the formatting is acceptable. Reject
ungrounded claims. Sanitize output.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `draft` | `AnswerDraft` | from `AnswerGenerator` |
| `profile` | `ResultProfile` | from `ResultAnalyzer` |
| `presentation` | `PresentationPlan` | from `PresentationPlanner` |

### Output: `ValidatedAnswer`

```text
text: str
accepted_claims: tuple[Claim, ...]
rejected_claims: tuple[Claim, ...]
sanitized: bool
format_ok: bool
warnings: tuple[str, ...]
```

### Errors

- `AnswerValidationError` with code:
  - `ANV.UNGROUNDED_CLAIM`
  - `ANV.NUMERIC_MISMATCH`
  - `ANV.FORMAT_VIOLATION`

### Side Effects

- Log event only.

### Dependencies

- Typed value objects.

### Forbidden Dependencies

- `LLMRouter` (deterministic checks only; LLM-judge variant deferred).
- `QueryExecutor`, `AgentState`.

### State Interaction

None.

### Deterministic / LLM

Deterministic.

### Security Sensitivity

Medium. Stops ungrounded claims and unsafe markup from reaching the API.

---

## 15. SQLRepairer

**Layer**: Domain (LLM-assisted).
**ADR**: ADR-001.
**Stage**: bounded retry loop.

### Purpose

Given a `SQLCandidate` and one or more validator reports, return a new
`SQLCandidate` that addresses the diagnostic. Bounded by
`SQLState.max_correction_attempts`.

### Input

| Parameter | Type | Source |
|-----------|------|--------|
| `candidate` | `SQLCandidate` | failing candidate |
| `validation` | `ValidationReport \| None` | from `SQLValidator` |
| `security` | `SecurityDecision` | from `SecurityValidator` |
| `semantic` | `SemanticReport \| None` | from `SemanticValidator` |
| `complexity` | `ComplexityReport \| None` | from `ComplexityValidator` |
| `plan` | `QueryPlan` | from `QueryPlanner` |
| `attempt` | `int` | orchestrator-tracked |

### Output: `SQLCandidate`

A new candidate. Same shape as `SQLGenerator` output.

### Errors

- `RepairError` with code:
  - `RPR.EXHAUSTED` — `attempt >= max_correction_attempts`.
  - `RPR.NO_RECOVERY` — diagnostics do not suggest a fix.
  - `RPR.LLM_UNAVAILABLE`.

### Side Effects

- One `LLMRouter.generate(task="sql_repair", ...)` call.
- Log events: `sql_repair_started`, `sql_repair_completed`,
  `sql_repair_failed`.

### Dependencies

- `LLMRouter`.
- `QueryPlan`, validator reports.

### Forbidden Dependencies

- **MUST NEVER** invoke `SecurityValidator` and discard the result; the
  repair output MUST be revalidated.
- **MUST NEVER** directly call `QueryExecutor`.
- **MUST NEVER** silently mutate SQL that violates security policy.
- **MUST NEVER** retry without bound; bounded by `SQLState.max_correction_attempts`.

### State Interaction

None. Returns `SQLCandidate`.

### Deterministic / LLM

LLM-assisted.

### Security Sensitivity

**Critical.** Repair MUST NOT bypass security. The repaired candidate
re-enters the full validation pipeline (SQL → SecurityValidator → SQLValidator
→ SemanticValidator → ComplexityValidator → QueryExecutor).

---

## 16. MemoryManager

**Layer**: Infrastructure.
**ADR**: ADR-009.
**Status today**: implemented as `src/memory/manager.py`.

### Purpose

Persist typed turns per session and provide bounded, ordered history.
Provide a `clear_session` and an `expire` hook (Sprint 11).

### Input

| Method | Input | Output |
|---------|-------|--------|
| `create_session()` | none | `session_id: str` |
| `session_exists(session_id)` | `str` | `bool` |
| `append_turn(session_id, turn)` | `Turn` | `None` |
| `get_history(session_id, limit, order)` | `str, int, Order` | `tuple[Turn, ...]` |
| `clear_session(session_id)` | `str` | `None` |
| `expire_older_than(timestamp)` | `datetime` | count deleted |

`Turn` is a frozen dataclass with `role: str`, `content: str`, `created_at: datetime`,
`metadata: dict`.

### Errors

- `MemoryError` with code:
  - `MEM.UNAVAILABLE`
  - `MEM.SESSION_LOST`

### Side Effects

- SQLite reads and writes.
- File-system writes (the SQLite file).
- Log events.

### Dependencies

- `sqlite3` stdlib.

### Forbidden Dependencies

- `LLMRouter` — memory is deterministic.
- `QueryExecutor` (separate DB connection).
- `AgentState` — the orchestrator reads/writes `conversation` from/to memory.

### State Interaction

None. The orchestrator reads history into `AgentState.conversation` and
calls `append_turn` at the end of the request.

### Deterministic / LLM

Deterministic, persistence-backed.

### Security Sensitivity

Low. Sessions are user-scoped. PII redaction happens at the AgentState
boundary; the MemoryManager stores raw content under the user-controlled
session key.

---

## 17. LLMRouter

**Layer**: Infrastructure / External Adapter.
**ADR**: ADR-011.
**Status today**: missing; current `LLMClient` is a thin facade.

### Purpose

Single entry point for all LLM calls. Owns provider, model, retries,
budgets, and observability. Domain components do not know about
providers.

### Input

| Parameter | Type | Notes |
|-----------|------|-------|
| `task` | `str` | `"sql_generation"`, `"planning"`, `"question_analysis"`, `"sql_repair"`, `"answer_generation"`, ... |
| `prompt` | `str` (or structured prompt) | rendered prompt text |
| `budget` | `LLMBudget` | optional token/time budget |
| `policy` | `LLMPolicy` | provider preference, fallback chain |

### Output: `LLMResult`

```text
text: str
provider: str                                   # e.g. "openai"
model: str                                      # e.g. "gpt-4o-mini"
usage: Usage                                    # prompt_tokens, completion_tokens, total_tokens, cost
latency_ms: float
metadata: dict[str, Any]
```

### Errors

- `LLMRouterError` with code:
  - `ROU.PROVIDER_UNAVAILABLE`
  - `ROU.BUDGET_EXCEEDED`
  - `ROU.NO_PROVIDER` — no provider matched the policy.
  - `ROU.TIMEOUT`

### Side Effects

- One HTTP request to the chosen provider.
- Log events: `llm_call_started`, `llm_call_completed`, `llm_call_failed`.
- Updates `RuntimeMetadata.llm_calls`, `token_usage`, `model_provider`,
  `model_name`.

### Dependencies

- Provider SDKs (`OpenAIClient`, `GeminiClient`, `GroqClient`, `OllamaClient`).
- Configuration (`OPENAI_API_KEY`, etc.).
- `log_event`.

### Forbidden Dependencies

- `SQLGenerator`, `QueryPlanner`, `QuestionAnalyzer`, etc. — the router
  does not know about domain components.
- `QueryExecutor`.
- `AgentState`.

### State Interaction

None. The orchestrator reads `LLMResult.usage` and calls
`state.with_llm_call(...)` to update `RuntimeMetadata`.

### Deterministic / LLM

LLM (this component is the LLM call itself).

### Security Sensitivity

Medium. Holds API keys. Must not log raw prompts without redaction.

---

## 18. Side-effect Matrix

This matrix records what side effects each component **may** have. A
component whose row says "No" must not perform that side effect, and any
future architecture test must enforce this.

Legend:
- LLM = calls `LLMRouter`
- DB R = reads from SQL Server (only `QueryExecutor` may do this)
- DB W = writes to SQL Server (none — read-only policy)
- MEM = reads/writes `MemoryManager` SQLite
- FS = local file system (logs, cache)
- NET = outbound network (HTTP, LLM providers)
- STATE = mutates `AgentState` directly (none — only the orchestrator)
- DET = deterministic / pure

| Component | LLM | DB R | DB W | MEM | FS | NET | STATE | DET |
|-----------|:---:|:----:|:----:|:---:|:--:|:---:|:-----:|:---:|
| QuestionAnalyzer | yes | no | no | no | no | (via router) | no | no |
| SchemaCatalog | no | yes (admin) | no | no | yes | no | no | yes |
| SchemaRetriever | no | no | no | no | no | no | no | yes |
| QueryPlanner | yes | no | no | no | no | (via router) | no | no |
| SQLGenerator | yes | no | no | no | no | (via router) | no | no |
| SQLValidator | no | no | no | no | no | no | no | yes |
| SecurityValidator | no | no | no | no | no | no | no | yes |
| SemanticValidator | no | no | no | no | no | no | no | yes |
| ComplexityValidator | no | no | no | no | no | no | no | yes |
| SQLRepairer | yes | no | no | no | no | (via router) | no | no |
| QueryExecutor | no | yes | no | no | no | no | no | yes |
| ResultAnalyzer | no | no | no | no | no | no | no | yes |
| PresentationPlanner | no | no | no | no | no | no | no | yes |
| AnswerGenerator | yes | no | no | no | no | (via router) | no | no |
| AnswerValidator | no | no | no | no | no | no | no | yes |
| MemoryManager | no | no | no | yes | yes | no | no | yes |
| LLMRouter | (is LLM) | no | no | no | (log) | yes | no | no |
| TraceContext / StructuredLogger | no | no | no | no | yes | no | no | yes |

**Rule**: Only `QueryExecutor` may read the database. No component may
write the database. No component may mutate `AgentState` directly — that
is the orchestrator's job. No component other than `LLMRouter` may open
a network socket.

---

## 19. Dependency Matrix

For each component, the matrix below lists dependencies that are
**allowed** and dependencies that are **forbidden**. The matrix is the
basis for future architecture tests.

| Component | May depend on | Must NOT depend on |
|-----------|---------------|--------------------|
| QuestionAnalyzer | `LLMRouter`, `ConversationContext`, `log_event` | `SQLGenerator`, `QueryPlanner`, `QueryExecutor`, `MemoryManager`, `AgentState`, `pymssql`, `sqlite3`, `OpenAIClient`, `GeminiClient`, `GroqClient`, `OllamaClient` |
| SchemaCatalog | `pymssql` (admin connection), file cache, `log_event` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| SchemaRetriever | `SchemaSnapshot`, `QuestionSpec` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| QueryPlanner | `LLMRouter`, `QuestionSpec`, `SchemaContext` | `SQLGenerator`, `QueryExecutor`, `MemoryManager`, `AgentState`, `pymssql` |
| SQLGenerator | `LLMRouter`, `QueryPlan`, `SchemaContext` | `QueryExecutor`, `SecurityValidator`, `SQLValidator`, `SemanticValidator`, `AgentState`, `pymssql`, direct provider clients |
| SQLValidator | `sqlglot` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| SecurityValidator | `sqlglot`, `SecurityPolicy` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| SemanticValidator | parsed AST, `QueryPlan`, `SchemaSnapshot` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| ComplexityValidator | parsed AST, `ComplexityBudget` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| SQLRepairer | `LLMRouter`, `QueryPlan`, validator reports | bypass of `SecurityValidator`, direct `QueryExecutor`, `AgentState`, direct provider clients |
| QueryExecutor | `pymssql`, `ExecutionPolicy`, `SecurityValidator` integration hook | `LLMRouter`, `MemoryManager`, `SchemaCatalog`, `AgentState` |
| ResultAnalyzer | `QueryResult`, `QueryPlan` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| PresentationPlanner | `QuestionSpec`, `ResultProfile`, `LocalePolicy`, `SchemaSnapshot.labels` | `LLMRouter`, `QueryExecutor`, `AnswerGenerator`, `AgentState` |
| AnswerGenerator | `LLMRouter`, `QueryResult`, `ResultProfile`, `PresentationPlan`, `QuestionSpec` | `QueryExecutor`, `MemoryManager`, `AgentState`, direct provider clients |
| AnswerValidator | `AnswerDraft`, `ResultProfile`, `PresentationPlan` | `LLMRouter`, `QueryExecutor`, `AgentState` |
| MemoryManager | `sqlite3` | `LLMRouter`, `QueryExecutor`, `SchemaCatalog`, `AgentState` |
| LLMRouter | provider SDKs, `log_event`, config | `SQLGenerator`, `QueryPlanner`, `QuestionAnalyzer`, `AnswerGenerator`, `SQLRepairer`, `QueryExecutor`, `AgentState` |

**Circular dependency check**: the matrix above is acyclic by construction
(LLMRouter is at the bottom; Orchestrator is at the top). Future
architecture tests will fail if any forbidden dependency appears in code.

---

## 20. Contract Completeness Review

Each contract must answer ten questions. The table below records the
answer status. Where the answer is "open", the question is genuinely
deferred and recorded in §15 of `interface-design.md`.

| Component | What does it do? | What does it NOT do? | What does it receive? | What does it return? | What can fail? | Dependencies | Side effects | State interaction | Observability | Deterministic? | Security-sensitive? |
|-----------|:----------------:|:--------------------:|:--------------------:|:-------------------:|:--------------:|:-------------:|:-------------:|:-----------------:|:-------------:|:--------------:|:-------------------:|
| QuestionAnalyzer | yes | yes | yes | yes | yes | yes | yes | yes | yes | LLM | no |
| SchemaCatalog | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | yes |
| SchemaRetriever | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| QueryPlanner | yes | yes | yes | yes | yes | yes | yes | n/a | yes | LLM | no |
| SQLGenerator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | LLM | no |
| SQLValidator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| SecurityValidator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | **yes** |
| SemanticValidator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| ComplexityValidator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| QueryExecutor | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | **yes** |
| ResultAnalyzer | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| PresentationPlanner | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| AnswerGenerator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | LLM | no |
| AnswerValidator | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | yes |
| SQLRepairer | yes | yes | yes | yes | yes | yes | yes | n/a | yes | LLM | **yes** |
| MemoryManager | yes | yes | yes | yes | yes | yes | yes | n/a | yes | yes | no |
| LLMRouter | yes | yes | yes | yes | yes | yes | yes | n/a | yes | is LLM | yes |

All 17 contracts answer all 10 questions. No contract has an "unknown"
cell. Future implementation per component will fail architecture tests if
it violates any "must not" or "forbidden dependency" rule.

---

## 21. Forward Compatibility Notes

- The contracts are written without Python class signatures because no
  implementation is created in this prompt.
- Each contract names the typed value objects (`QuestionSpec`, `QueryPlan`,
  etc.) that will be defined alongside implementations in future prompts.
- The error code catalog in `interface-design.md` §7.4 is the canonical
  code registry; new codes must be added there before any component uses
  them.
- The "Retry policy" rule is enforced by the orchestrator; components do
  not retry themselves. `SQLRepairer` is the only component that
  intentionally retries the generation step, and it is bounded.
- The contract set intentionally does not include a "Planner
  verification" component; verification is the role of `SemanticValidator`
  + `ComplexityValidator`. This avoids duplicating semantic checks.