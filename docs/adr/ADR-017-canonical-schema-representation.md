# ADR-017: Canonical Schema Representation & Schema Intelligence Boundaries

**Status**: Accepted — implemented in Sprint 3.1 / Prompt 3.1B (design approved in Prompt 3.1A)
**Supersedes**: none (refines ADR-002 and dependency-rules.md §3.2)
**Companion documents**:
- [`docs/adr/ADR-002-schema-intelligence-subsystem.md`](ADR-002-schema-intelligence-subsystem.md) — accepted principle: a live/versioned SchemaCatalog owns schema truth
- [`docs/architecture/schema-intelligence.md`](../architecture/schema-intelligence.md) — full design
- [`docs/contracts/schema-intelligence.md`](../contracts/schema-intelligence.md) — contract reconciliation
- [`docs/audit/schema-intelligence-current-state.md`](../audit/schema-intelligence-current-state.md) — evidence base

## Context

Sprint 0/1/2 audits proved the current schema knowledge is fragmented and drifting: `FULL_SCHEMA` (the prompt's only schema source) omits `Sale.Shipments` entirely and claims a `buyerId → Buyers` relationship that does not exist; the database declares **no primary-key or foreign-key constraints**, so relationships exist only as annotated knowledge; Persian labels are LLM-generated per run, keyed by ambiguous bare column names, contain stale entries, and are currently loaded but never consumed; the "schema hash" is an MD5 column-map cache key, not a schema identity; no mechanism detects new/removed tables or relationship changes. Contracts for `SchemaSnapshot`/`SchemaCatalog`/`SchemaRetriever` exist and are tested, but no production component supplies a real snapshot.

## Decision

1. **Source-of-truth hierarchy (refines ADR-002)**: SQL Server system metadata is authoritative for structure; user-authored, reviewed annotations are authoritative for semantics (descriptions, formulas, Persian labels); `SchemaCatalog` is the single canonical runtime owner that merges both into immutable, versioned `SchemaSnapshot` value objects. Prompts, retrieval and presentation are **consumers, never truth**.

2. **Snapshot identity = content fingerprint**: `SchemaVersion.id` is the sha256 of the snapshot's canonical serialization (deterministic ordering rules: tables by qualified name, columns by ordinal position, relationships/semantics/labels by defined keys; `captured_at` excluded from the hash). The legacy MD5 remains only the column-map cache key until that pipeline retires.

3. **Package placement**: implementation lives in `src/core/schema/` (`catalog.py`, later `retriever.py`), mirroring the ADR-016 `src/core/llm/` pattern. Value objects stay canonically in `src/agents/contracts.py` — `ColumnDescriptor` is the only new VO; `TableDescriptor`/`Relationship` gain additive optional fields (`column_details`, `description`; `kind`, `cardinality`); the `SchemaCatalog` Protocol gains additive `refresh()`.

4. **Database boundary (refines dependency-rules §3.2)**: SchemaCatalog keeps its sanctioned refresh-time read-only admin connection, but reaches it through a metadata adapter, `src/core/database/metadata.py` — the only new `pymssql` import site. `src/core/schema/**` must not import the driver, the LLM layer, provider SDKs, `src.agents.state`, `src.api/*` or `src.memory`; all enforced by repository-native architecture tests. SchemaCatalog never executes user SQL and never uses the per-request execution path.

5. **LLM boundary**: structural schema discovery is fully deterministic and requires no LLM. The legacy LLM-based column-map generator is deprecated by this decision (its non-determinism and stale output are documented defects). Any future LLM semantic enrichment must go through `LLMRouter` and land as reviewed annotations, never as live truth.

6. **Refresh/cache strategy (resolves Sprint-2 open questions Q2/Sprint5 and Q10)**: startup refresh (probe-then-discover, one cheap query in the steady state), explicit `refresh()`, no scheduler; refresh failure serves the cached snapshot stale-but-flagged, or raises typed `SCH.UNAVAILABLE` with no cache. Cache is a local, catalog-owned, content-addressed JSON file; no external cache.

7. **ColumnMapper boundary**: schema meaning (descriptions, semantics) and Persian display labels are **different concepts** — labels are presentation metadata carried as table-qualified keys in `SchemaSnapshot.labels` (existing contract field, per component-contracts §2 / dependency-rules §3.12); ColumnMapper is legacy presentation-only and is not merged into semantics. Term-level vocabulary ("buyer" = customer) belongs to the future QuestionAnalyzer vocabulary, not the catalog.

8. **AgentState unchanged**: `AgentState.schema_context` remains the only schema-related state field, written exclusively by the Orchestrator via the existing `with_schema_context` applier. The catalog and snapshots never enter AgentState.

## Consequences

- No production behavior changes by this decision alone; implementation (3.1B+) is additive and the live pipeline remains byte-compatible at every step.
- All contract changes are additive frozen-dataclass defaults; existing tests stay valid; `contracts.py` remains the sole definition surface (ADR-012/016 unchanged).
- Drift detection becomes complete (every change class in the audit matrix produces a new version) instead of column-only.
- Migration debt recorded: static `models.py` exports, `FULL_SCHEMA`, column-map pipeline and `schema_hash.py` are deprecated in place and removed only in later explicitly-authorized stages.

## Implementation status (Sprint 3.1B)

Implemented exactly as decided, with these documented refinements:

* **Driver import is lazy** in `src/core/database/metadata.py` (`pymssql` imported inside the reader constructor when no `connect_fn` is injected — the ADR-016 lazy-provider pattern), so importing `src.core.schema.catalog` loads no driver.
* **The catalog's startup refresh trigger is first access** (`current_version()`/`versions()`/`get_snapshot()`/`refresh()`), not `__init__`: construction performs local file reads only (annotations + cache). This keeps 3.1B strictly non-disruptive — the façade constructs the catalog, nothing in the live pipeline consumes it yet, so no new database call exists anywhere at runtime. The refresh *flow* is exactly the approved probe-then-discover design.
* **Annotations are re-checked at every refresh** (file hash participates in the probe signature), so user-authored annotation edits create new versions without database changes.
* **Fingerprint input excludes the whole version block** (`captured_at` and `source`): identity is pure structural + annotated content — the only coherent reading of "identity = content", since the version block cannot hash itself.
* **Composite PKs/FKs**: one `Relationship` per column pair (matched by ordinal); a composite PK records all its columns as `key_role="pk"` with `TableDescriptor.primary_key` holding the lowest-ordinal column (the contract field is single-valued).

**Sprint 3.1C addendum**: SchemaRetriever implemented (`src/core/schema/retriever.py`) per the approved contracts — deterministic scoring, bounded selection, snapshot-relationship-only join paths, `SCH.AMBIGUOUS`/`SCH.NO_RELEVANT_SCHEMA` as context warnings. The catalog/retriever seams are wired through `OrchestratorDeps` but remain **dormant** until a QuestionAnalyzer supplies a QuestionSpec (no hidden question-analysis behavior). One additive AgentState transition (`SCHEMA_RESOLVED → SQL_GENERATED`, the established direct-path pattern) enables the seam to complete without a QueryPlanner; live request behavior is unchanged.

**Phase 3.2 addendum**: the retriever became a production retrieval boundary while staying dormant on the live path (Case B re-confirmed — no QuestionAnalyzer exists). Documented refinements within the approved design:

* **Query-type-aware evidence routing** — filter fields are column-scoped evidence (never table-name/description evidence); untyped entities and metric names remain dual-surface; `time_constraints`/`references` contribute nothing (QuestionAnalyzer prerequisite). Justification: a filter constrains a field, it does not name a table.
* **Hard context bounds + deterministic budgeting** — `RetrievalPolicy` gains `max_join_hints`/`max_semantic_entries`/`max_context_chars` (canonical serialized size; no token estimate — no estimator exists, provider-specific token semantics are not invented). Trimming priority: semantics (cap, then reverse-canonical), then non-endpoint columns lowest-score-first; tables, join hints, endpoint columns and warnings are never removed; any trimming or structural-floor breach is reported via the new `SCH.CONTEXT_BOUNDED` context warning (registered in `interface-design.md` §7.4).
* **SchemaContextRenderer** (`src/core/schema/renderer.py`) — the Phase 12/13 controlled rendering boundary (`SchemaContext → LLM schema payload`): formatting only, context-only input, no DB/catalog/analyzer/router imports, deterministic, hard size limit with documented section-drop priority and typed fail-closed error. Dormant until the separately-authorized prompt-migration stage; `FULL_SCHEMA` is deliberately **not** retired in this phase.
* **Telemetry/runtime metadata** — the retriever emits the contract-named `schema_retrieval_completed` event (counts/codes/sizes only); `RuntimeMetadata` gains an additive `schema_metrics` dict written only by the Orchestrator seam when retrieval runs (no second telemetry model).
* **Leakage prevention + evaluation** — executable tests pin the legacy `FULL_SCHEMA` import surface to `src/core/prompts.py` alone, forbid it in retrieval/rendering/orchestrator modules, and prove the SQLGenerator seam receives the retrieved context only; a 15-case exact-expectation evaluation dataset (built on the real catalog snapshot) yields precision/recall 1.0, bounded-context compliance 100% and protected-path full-schema leakage 0%.

## Alternatives considered

- **Direct pymssql in SchemaCatalog** (as dependency-rules §3.2 originally allowed) — rejected: contradicts the adapter pattern ADR-016 established; a schema-side driver import would create a second DB-boundary door.
- **Evolving `TableDescriptor.columns` to descriptors (breaking)** — rejected: additive `column_details` with a names-match invariant achieves the same capability with zero compatibility cost.
- **Precomputed retrieval fields / alias ontology in the snapshot** — rejected: no demonstrated consumer; retrieval-derived indexes belong to the retriever.
- **Periodic background refresh / external cache** — rejected: unsupported operational complexity for a 3-table metadata set; load-once/refresh-explicitly is sufficient.
