# Target Responsibilities

> Updated in Sprint 2 Prompt 2A to align with the contracts in
> `docs/contracts/component-contracts.md` and
> `docs/architecture/dependency-rules.md`.

---

## Layered responsibilities

Each target component has a single primary responsibility and explicit
non-responsibilities. The non-responsibilities are what prevent
components from becoming hidden God Objects.

### Analyzer MUST NOT

- generate SQL,
- call the DB,
- write memory,
- invoke validators,
- mutate `AgentState`.

### Catalog (SchemaCatalog) MUST

- own live versioned schema metadata,
- expose immutable snapshots,
- be the single source of truth for schema.

### Catalog MUST NOT

- execute user SQL,
- be called from `QueryExecutor`,
- mutate `AgentState`.

### Retriever (SchemaRetriever) MUST NOT

- generate SQL,
- call the DB,
- mutate `AgentState`,
- depend on `LLMRouter` (retrieval is deterministic).

### Planner (QueryPlanner) MUST

- express intent, joins, metrics, filters, grouping, limits,
  assumptions explicitly,
- consume `QuestionSpec` + `SchemaContext`,
- return a typed `QueryPlan`.

### Planner MUST NOT

- generate SQL,
- execute anything,
- mutate `AgentState`.

### Generator (SQLGenerator) MUST

- emit SQL candidates only,
- consume `QueryPlan` + `SchemaContext`.

### Generator MUST NOT

- validate security,
- execute SQL,
- retrieve schema (use `SchemaRetriever` instead),
- write memory,
- format user-facing answers,
- mutate `AgentState`.

### SQLValidator (syntax only) MUST

- validate AST/dialect,
- never generate, modify or execute SQL,
- never raise security errors.

### SecurityValidator MUST

- fail closed,
- accept exactly one read-only statement,
- be the authoritative security decision.

### SemanticValidator MUST

- check joins, filters, aggregates, units, currencies, business status,
- report ambiguity rather than silently changing intent.

### ComplexityValidator MUST

- estimate cost,
- reject candidates that exceed `ComplexityBudget`.

### Repairer (SQLRepairer) MUST

- be bounded by `SQLState.max_correction_attempts`,
- revalidate every repaired candidate,
- return a new `SQLCandidate`.

### Repairer MUST NOT

- bypass validators,
- mutate SQL that violates security,
- execute SQL,
- retry without bound.

### QueryExecutor MUST

- be the sole DB boundary (ADR-006),
- enforce read-only credentials, timeout, row/column caps, request isolation,
- never mutate `AgentState`.

### QueryExecutor MUST NOT

- bypass `SecurityValidator` (integration is documented as a
  pre-condition; the existing inline security check in
  `DatabaseConnection.execute_query` is preserved),
- call any LLM,
- call `MemoryManager`.

### ResultAnalyzer MUST

- inspect rows, columns, types, nulls, counts, truncation, aggregate
  provenance,
- produce deterministic `ResultProfile`.

### ResultAnalyzer MUST NOT

- call the DB or LLM,
- mutate `AgentState`.

### PresentationPlanner MUST

- select safe columns and formats,
- decide view kind (table / KPI / ranking / timeseries),
- produce `PresentationPlan`.

### PresentationPlanner MUST NOT

- execute SQL,
- change SQL,
- invent data,
- perform business calculations not defined by the contract,
- mutate `AgentState`.

### AnswerGenerator MUST

- phrase supplied facts only,
- consume `QuestionSpec`, `ResultProfile`, `PresentationPlan`,
  `QueryResult`, and optional evidence,
- emit `AnswerDraft` with explicit claims.

### AnswerGenerator MUST NOT

- query the DB,
- bypass `AnswerValidator`,
- write memory,
- mutate `AgentState`.

### AnswerValidator MUST

- check claims against `ResultProfile`,
- sanitize output,
- reject ungrounded claims.

### MemoryManager MUST

- store typed turns with provenance,
- retain ordered recent turns + bounded summaries,
- apply retention policy.

### MemoryManager MUST NOT

- own `AgentState`,
- own schema metadata,
- own SQL execution,
- call `LLMRouter`.

### LLMRouter MUST

- own provider, model, retry and budget policy,
- emit usage and latency telemetry,
- be provider-neutral from the domain's point of view.

### LLMRouter MUST NOT

- know about SQL or schema,
- know about components,
- mutate `AgentState`.

### Cross-cutting (Observability, TraceContext, StructuredLogger) MUST

- be available to every component,
- not own business logic,
- never expose raw exception text.

### Cross-cutting MUST NOT

- be implemented as a logger inside a domain component,
- import vendor SDKs.

---

## Sole-writer rule

Only the Orchestrator writes `AgentState` (ADR-013). Components return
typed results; the Orchestrator applies them.

## Single database boundary

Only `QueryExecutor` reads from SQL Server (ADR-006).

## Single LLM boundary

Only `LLMRouter` calls an LLM provider (ADR-011).

## Single security boundary

Only `SecurityValidator` makes security decisions (ADR-004).

---

These rules are the contract set's shorthand. For the full per-component
contract (input types, output types, error codes, dependencies, side
effects, state interaction, determinism, security sensitivity) see
[`docs/contracts/component-contracts.md`](../contracts/component-contracts.md).