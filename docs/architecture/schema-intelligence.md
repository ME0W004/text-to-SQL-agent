# Schema Intelligence — Target Architecture

**Sprint**: 3.1 — Prompt 3.1A (design) / **Prompt 3.1B (implementation)**
**Status**: **Implemented in Prompts 3.1B–3.1C, hardened in Phase 3.2** (catalog + metadata adapter + annotations in 3.1B; SchemaRetriever + dormant seam wiring in 3.1C; bounded/budgeted retrieval boundary + rendering boundary + telemetry + evaluation in Phase 3.2); see "Implementation status" at the end of this document
**Decision records**: [`ADR-002`](../adr/ADR-002-schema-intelligence-subsystem.md) (accepted — SchemaCatalog principle), [`ADR-017`](../adr/ADR-017-canonical-schema-representation.md) (accepted — canonical representation & boundaries; records 3.1B implementation refinements)
**Companion documents**:
- [`docs/audit/schema-intelligence-current-state.md`](../audit/schema-intelligence-current-state.md) — evidence base (sources, inventory, drift, duplication)
- [`docs/contracts/schema-intelligence.md`](../contracts/schema-intelligence.md) — contract-level design
- [`docs/architecture/dependency-rules.md`](dependency-rules.md) — §3.2/§3.3, §4.1 (pre-authorized dependencies)
- [`docs/contracts/component-contracts.md`](../contracts/component-contracts.md) — §2 SchemaCatalog, §3 SchemaRetriever (authoritative contracts)

Design rule applied throughout: **every proposed field, method and module has a demonstrated reason** (current defect, existing contract obligation, or documented future consumer). Hypothetical-optimization fields were evaluated and explicitly rejected (§13).

---

## 1. Goal

Replace the proven-drifting static/hand/LLM-authored schema knowledge (see audit §2) with one canonical, versioned, deterministic schema representation — and keep the existing pipeline running unchanged while it is introduced incrementally.

## 2. Source-of-Truth Hierarchy (3.1A.19)

```
SQL Server system metadata  (INFORMATION_SCHEMA / catalog views)      AUTHORITATIVE — structural
        │  read-only admin introspection, refresh-time only
        ▼
Database metadata adapter  (src/core/database/ — the only new driver site)
        │  driver-free typed rows
        ▼
SchemaCatalog  (src/core/schema/)                                     CANONICAL OWNER — runtime
        │  merges structural discovery + user-authored semantic annotations
        │  owns refresh, cache, versioning; produces immutable snapshots
        ▼
SchemaSnapshot  (value object, canonical definition in contracts.py)  CANONICAL REPRESENTATION
        │  requested by the Orchestrator, never stored in AgentState
        ▼
SchemaRetriever  (src/core/schema/)                                   SELECTION — request-scoped
        │  QuestionSpec + SchemaSnapshot → SchemaContext
        ▼
SchemaContext  (AgentState.schema_context via Orchestrator applier)   REQUEST SUBSET
        │
        ▼
Prompt representation  (rendered text)                                DERIVED — consumers, not truth
```

Authoritativeness classes (3.1A.19):

| Class | What | Owner |
|---|---|---|
| Authoritative (structural) | tables, columns, types, nullability, key roles — as the DB actually is | SQL Server metadata, read by the introspection adapter |
| User-authored (semantic) | column/table descriptions, business formulas, Persian labels | annotation file(s), reviewed by humans, versioned **with** the snapshot |
| Canonical runtime state | the merged, versioned snapshot | `SchemaCatalog` (single writer of catalog state) |
| Derived | prompt text, retrieval indexes | rendered/computed **from** a snapshot, never hand-maintained alongside it |
| Cached | serialized snapshot on disk | catalog-owned cache; always rebuildable from DB + annotations |
| LLM-generated | **nothing at runtime** | the legacy column-map generator is the only LLM-derived artifact today; it is deprecated by this design (§12). If semantic enrichment via LLM is ever added, it goes through `LLMRouter` and lands as a *reviewed annotation*, never as a live truth source |

## 3. Package Placement (3.1A.32)

Per ADR-016 ("no artificial layering; existing top-level packages retained"), Schema Intelligence goes into **`src/core/schema/`** — mirroring the approved `src/core/llm/` pattern (leaf module for value objects is not needed: schema VOs already live canonically in `contracts.py`):

```
src/core/schema/
  catalog.py        # SchemaCatalog implementation (owns refresh/cache/versioning)
  retriever.py      # SchemaRetriever implementation (pure selection, 3.1C)
src/core/database/
  metadata.py       # NEW introspection adapter — the only new pymssql import site
```

Rejected placements:
* `src/domain/`, `src/application/` — packages that do not exist; creating them contradicts ADR-016.
* `src/schemas/` — vestigial empty package; wrong name-space, zero content, would add confusion.
* Inside `src/core/database/` — the catalog is *not* an execution concern; only the metadata adapter belongs at the database boundary.

Respected boundaries:
* **Database boundary (3.1A.25)**: `src/core/schema/*` must not import `pymssql`. Structural discovery crosses the boundary through `src/core/database/metadata.py` (the adapter), which returns driver-free typed rows. This refines dependency-rules §3.2 (written before ADR-016 allowed SchemaCatalog a direct admin connection): the admin connection is still SchemaCatalog's to open, but via the adapter — same access right, cleaner direction. ADR-017 records this refinement.
* **LLM boundary (3.1A.26)**: `src/core/schema/*` must not import `src.core.llm.*` or any provider module. Structural discovery is fully deterministic; no LLM exists anywhere in the schema path.
* **AgentState boundary (3.1A.23)**: `src/core/schema/*` must not import `src.agents.state`. Snapshots/contexts cross as contract VOs.
* Dependency direction: `schema/` → `database/metadata.py` → driver; `contracts.py` ← both. No cycle (database never imports schema).

## 4. SchemaCatalog Design (3.1A.11)

| Aspect | Decision |
|---|---|
| Responsibility | Own the current, versioned `SchemaSnapshot`; merge structural discovery with semantic annotations; own refresh, cache and version history. **Single writer** of catalog state. |
| Input source | (a) `database.metadata` adapter (structural, refresh-time); (b) user-authored semantic annotations (static file, e.g. `data/schema/annotations.json` — exact format fixed in 3.1B) |
| Output | immutable `SchemaSnapshot` VOs (read surface per existing Protocol: `current_version()`, `versions()`, `get_snapshot(version=None)`); `refresh()` per component-contracts §2 |
| Lifecycle | constructed once by the composition root (façade), like `DatabaseConnection` today |
| Refresh (3.1A.20) | **startup refresh** (one bounded introspection pass at construction, replacing the current column-map check slot) + **explicit `refresh()`** + **hash-mismatch-triggered refresh** (cheap probe → full discovery only on change). **No scheduler, no periodic background refresh.** This resolves Sprint-2 open question Q10 ("startup or admin-only") as: startup, bounded, plus explicit admin/tooling entry point. |
| Refresh failure behavior | if a cached snapshot exists → serve it stale-but-flagged (log warning, event `schema_refresh_failed`); if none exists → raise typed `SchemaError` (`SCH.UNAVAILABLE`). Never silently fabricate. |
| Cache (3.1A.21) | local JSON file (one file, content-addressed by version id), written on each successful refresh, read at startup to serve a snapshot without touching the DB. Invalidation: a new version id simply supersedes; bounded history retained (last N versions, N small, exact value in 3.1B). Corruption → treated as absent (rebuild from DB). Ownership: catalog. Serialization: canonical JSON (§12). **No external cache (no Redis).** Caching *is* needed: the design must "load once, reuse safely, refresh explicitly" (3.1A.28) and the system already caches (`.schema_cache.json` precedent). |
| Version | content fingerprint (§8) |
| Ownership | infrastructure layer; the Orchestrator receives it by injection (`OrchestratorDeps.schema_catalog` — the seam already exists, `orchestrator.py:146`) |
| Determinism | §12 |

Forbidden (per component-contracts §2, unchanged): `LLMRouter` (catalog is deterministic), `QueryExecutor` (separate connection/path), `MemoryManager`, `AgentState`.

## 5. Canonical SchemaSnapshot (3.1A.12)

Field-by-field evaluation (existing contract shape kept; extensions additive):

| Field | Keep/Change | Reason |
|---|---|---|
| `version: SchemaVersion` | keep | stable identity + provenance (`captured_at`, `source`) — required by SCH.VERSION_NOT_FOUND semantics and cache addressing |
| `tables: tuple[TableDescriptor, ...]` | keep, **extend** descriptor (§6) | structural core |
| `relationships: tuple[Relationship, ...]` | keep, **extend** (§7) | join planning + semantic validation need typed joins; DB declares none → annotation-provenance is mandatory to prevent false trust |
| `business_semantics: tuple[BusinessSemantics, ...]` | keep | revenue formula etc. are schema-tied facts that SemanticValidator/grounding will consume |
| `labels: Mapping[str, str]` | keep, **re-key** | presentation metadata (Persian display labels) belongs with the snapshot per existing contract (`PresentationPlanner` consumes `SchemaSnapshot.labels`, dependency-rules §3.12). Keys become table-qualified (`"Sale.OrderItems.quantity"`) — the current bare-name map cannot disambiguate `Id`/`quantity`/`status` across 3 tables (audit §2 S4). Bare-name lookup allowed as fallback when unambiguous. |
| ~~row statistics~~ | **rejected** | no current consumer; would couple catalog to data volumes; can be added later if retrieval proves it needs selectivity hints |
| ~~indexes~~ | **rejected** | DB has none **[S0]**; no consumer |
| ~~sensitivity classification~~ | **rejected as a field for now** | repository has no data-classification requirement today; documented as a future-compatible additive field on `ColumnDescriptor` only if such a need materializes (§11) |

## 6. TableDescriptor & ColumnDescriptor (3.1A.13, 3.1A.14)

`TableDescriptor` (existing; **additive** extension):

| Field | Status | Reason |
|---|---|---|
| `name: str` | keep | fully-qualified (`"Sale.Orders"` — pinned by existing test) |
| `columns: tuple[str, ...]` | keep | cheap name view for retrieval/context |
| `primary_key: Optional[str]` | keep | join planning; presentation drops key columns |
| `foreign_keys: tuple[Relationship, ...]` | keep | out-edges of this table |
| `column_details: tuple[ColumnDescriptor, ...] = ()` | **new, additive (default `()`)** | types/nullability/descriptions; additive default keeps every existing construction site and test valid |
| `description: Optional[str] = None` | **new, additive** | table-level business meaning (e.g. "physical shipments per order item") needed by retrieval and prompt rendering; currently exists only as prose nowhere |

Invariant (catalog-enforced): `tuple(c.name for c in column_details) == columns`.

`ColumnDescriptor` (**new value object**):

| Field | Reason |
|---|---|
| `name: str` | identity |
| `data_type: str` | type-aware SQL generation and drift detection (already introspected today, consumed by nothing — this fixes that) |
| `nullable: bool` | LEFT JOIN / IS NULL semantics; drift detection (already introspected) |
| `key_role: str` (`"none" \| "pk" \| "fk"`) | join planning + presentation filtering of `*Id` columns (documented behavior, audit §5) |
| `description: Optional[str] = None` | semantic grounding for retrieval & rendering; user-authored |

Explicitly evaluated and **rejected** for 3.1B (each had no demonstrated consumer):
* `default` — no query-generation or validation consumer; schema_hash does not cover it; revisit if a consumer appears.
* `aliases` — term-level knowledge ("buyer"=customer) belongs to the future QuestionAnalyzer vocabulary, not the schema (audit §6); putting it here would merge two boundaries the task explicitly separates (3.1A.4).
* `sensitivity` — future-compatible extension only (§11).
* normalized names / precomputed search text — §13.

## 7. Relationship Model (3.1A.15)

`Relationship` (existing; **additive** extensions):

| Field | Status | Reason |
|---|---|---|
| `from_table`, `from_column`, `to_table`, `to_column` | keep | the join path itself |
| `name: Optional[str]` | keep | named-relationship support (already in contract) |
| `kind: str = "annotated"` | **new, additive**; values `"database_constraint" \| "annotated" \| "inferred"` | the DB declares **no constraints** **[S0]** — trust must be proportionate to provenance; SemanticValidator must be able to weight `database_constraint` differently from `annotated` |
| `cardinality: Optional[str] = None` | **new, additive** | `"1:N"` where known (both known relationships are 1:N); prevents inventing semantics while recording what is documented |

No semantic relationships are invented: only the two explicitly documented ones enter the snapshot (audit §5).

## 8. Schema Versioning (3.1A.18)

Decision: **content fingerprint as version identity.**

* `SchemaVersion.id` = sha256 over the snapshot's canonical serialization (§12) — structural *and* annotated content. Same content ⇒ same id, regardless of dictionary/driver/process ordering.
* `captured_at` = generation timestamp (metadata, not identity).
* `source` = provenance string (e.g. `"introspection+annotations@<annotations-version>"`).
* A hash change means a new version; versions form bounded history enabling `get_snapshot(version)` and the `SCH.VERSION_NOT_FOUND` / `SCH.DRIFT` error semantics already specified in component-contracts §2.
* The legacy MD5 (`schema_hash.py`) remains exactly what it is today — a column-map cache key — until the column-map pipeline retires (§12). It is **not** schema identity and never was (audit §7). New code uses sha256; MD5 stays only in the untouched legacy path.

Evaluated and rejected: plain timestamp identity (non-deterministic), monotonic counter (meaningless across processes), migration-version (no migration tooling exists in this repo).

## 9. Refresh & Cache Contract (3.1A.20, 3.1A.21)

```
startup ──► load cache ──► hit? ──► serve snapshot (log version)      [no DB call]
                              │ miss
                              ▼
                        probe hash (1 cheap query)
                              │ changed / unknown
                              ▼
                        full introspection (metadata adapter)
                              ▼
                        merge annotations ──► build snapshot ──► fingerprint
                              ▼
                        persist cache, emit schema_refresh_completed
explicit refresh() ──► same path, forced
refresh failure ──► cached snapshot? serve stale + warn : SCH.UNAVAILABLE
```

* Probe-then-discover keeps startup cost at one `INFORMATION_SCHEMA.COLUMNS` query in the steady state (no worse than today's `get_schema_hash` call).
* Introspection uses a **separate read-only admin connection** opened by the metadata adapter at refresh time only — never the per-request execution path (component-contracts §2 Forbidden; dependency-rules §4.1 carve-out).
* The catalog must **never execute user SQL** during introspection (component-contracts §2 security note) — only constant introspection statements.
* Observability: `schema_refresh_started`, `schema_refresh_completed` (with version id + duration), `schema_refresh_failed` — via the shared `log_event` surface; payloads carry counts/version/duration only.

## 10. SchemaRetriever Readiness (3.1A.22)

The snapshot shape supports future retrieval without modification:
* **table/column relevance**: typed, filterable `TableDescriptor`/`ColumnDescriptor` collections;
* **relationship relevance**: `Relationship` edges (graph traversal for join paths);
* **business semantics**: `BusinessSemantics.applies_to` maps formulas to qualified columns;
* **question concepts**: column/table `description` fields carry the Persian/English grounding text retrieval will match against; `labels` provide the Persian surface forms.

Not added (no demonstrated use yet — 3.1A.31): precomputed search text, normalized names, token embeddings, popularity counters. The retriever may compute such indexes in-memory at process level; they must not become snapshot fields until a consumer exists.

## 11. Security / Sensitive Metadata Considerations (3.1A.27)

* The catalog contains **metadata only** — no row data is ever read (introspection reads system catalogs; no `SELECT * FROM Sale.*` anywhere in the design).
* Metadata sensitivity in this deployment: table/column names, Persian labels, and descriptions are acceptable for authorized product users (consistent with component-contracts §2 "Medium" rating). Descriptions must not embed credentials or raw customer PII — they describe *columns*, not *values* (the annotation format review in 3.1B checks this).
* `buyer`/`driver` name columns are flagged in descriptions as free-text personal-name holders — recorded as **descriptions** (facts), not as an invented classification system. A future `sensitivity` field on `ColumnDescriptor` is the documented additive extension point if a real classification requirement appears.
* Introspection is read-only; non-query execution stays disabled (`connection.py:222-227` semantics preserved at the adapter).
* Cache files contain metadata only; log events carry counts/versions/durations, never metadata dumps (existing logging policy applies unchanged).
* No bypass of Security Core: user SQL never touches the catalog path; introspection SQL is constant and never user-influenced.

## 12. Determinism Rules (3.1A.30)

Same DB metadata + same annotations ⇒ byte-identical snapshot & fingerprint. Canonical ordering (enforced before hashing/serializing):

| Entity | Order |
|---|---|
| tables | by fully-qualified name (string sort) |
| columns (and details) | by discovery `ORDINAL_POSITION` within table (DB-stable); annotation merge must not reorder |
| relationships | by `(from_table, from_column, to_table, to_column)` |
| business semantics | by `(applies_to, description)` |
| labels | by key |
| serialization | JSON with sorted keys, fixed separators, UTF-8; `captured_at` excluded from the fingerprint input (it is metadata, not content) |

Note: `ORDINAL_POSITION` ordering is deterministic per database state; the audit's requirement is that *dictionary ordering, driver row order and process order* cannot influence the result — sorting rules above guarantee that.

## 13. Retrieval-Efficiency Fields — Explicitly Rejected (3.1A.31)

`normalized_names`, `search_text`, `semantic_terms`, alias indexes: **not added**. Every one lacks a demonstrated consumer today; all are computable at retrieval time from descriptions/labels. This keeps the snapshot a faithful, minimal representation rather than a retrieval cache (retrieval caches belong to the retriever, which can be rebuilt freely).

## 14. Current vs Target Matrix (3.1A.34)

| Current mechanism | Responsibility today | Target owner | Migration strategy |
|---|---|---|---|
| `INFORMATION_SCHEMA.COLUMNS` reads (`schema_hash.py`, `generate_column_map.py`) | partial structural discovery for a cache key / label regen | `src/core/database/metadata.py` adapter (full: tables/columns/keys) | adapter introduced alongside; legacy callers untouched until retirement |
| `models.py` static dicts (`ORDERS_MODEL`…) | none (dead exports) | — (superseded) | keep untouched in 3.1B (zero consumers ⇒ zero risk); deprecate in docs; remove in a later cleanup stage |
| `build_compact_schema()` | none (dead) | — | same as above |
| `FULL_SCHEMA` | prompt truth (semantic + structural, drift-prone) | snapshot + prompt renderer derived from `SchemaContext` | seed catalog annotations **from** FULL_SCHEMA content (human-reviewed); retire when SQLGenerator consumes SchemaContext (Prompt 3.1C) |
| Prompt rules (join paths, revenue, Jalali) | query-generation metadata in text | relationships + business_semantics in snapshot; conventions move to future planner/generator | staged: catalog first, prompt untouched until 3.1C |
| Few-shots | convention demonstrations | unchanged in 3.1B; reviewed under 3.1C | keep |
| `COLUMN_MAP` + updater + generator script + `.schema_cache.json` | LLM-derived Persian labels (dead import) + startup regeneration | `SchemaSnapshot.labels` (user-authored annotations, table-qualified) | keep operating unchanged during 3.1B; deprecate once PresentationPlanner consumes `SchemaSnapshot.labels`; then remove pipeline (migration debt item) |
| `schema_hash.py` MD5 | cache key | version fingerprint (sha256, content-based) | legacy MD5 path untouched until column-map retirement |
| `AgentState.schema_context` | written with empty context only in stub path | request-scoped `SchemaContext` from retriever | **no change** — applier (`with_schema_context`) and stage transition already exist and are tested |
| Orchestrator `_run_schema_resolution` | no-op stub with injection seam | receives catalog+retriever via `OrchestratorDeps` | **no change required** — the wiring path is already implemented and tested |

## 15. Migration Strategy (3.1A.33)

Verdict per legacy component (nothing removed in 3.1A; removals happen only in later, explicitly-authorized stages):

| Component | Verdict |
|---|---|
| `database/models.py` dicts + `build_compact_schema` | **keep** (dead, harmless) → deprecate → **remove later** (cleanup stage) |
| `FULL_SCHEMA` | **keep** (live prompt dependency) → **wrap/migrate** (content becomes annotations) → deprecate at 3.1C |
| `schema_hash.py` | **keep** (column-map dependency) → deprecate with the pipeline → remove later |
| `column_mapper*.py`, generator script, `.schema_cache.json` | **keep** operating unchanged → deprecate when labels are catalog-served → remove later |
| prompt rules/few-shots | **keep** until SQLGenerator lands → migrate to plan/context-driven rendering |

Migration invariant: at every step the live pipeline (`/api/v1/ask`, CLI, evaluation) behaves identically; the catalog is introduced additively (new modules + injection), never by rewiring the live path in the same stage that introduces it.

## 16. Test Strategy Design (3.1A.29) — obligations for 3.1B

Repository-native pytest only (no new framework). Required suites (locations per existing layout):

1. **Unit (fake metadata — no live DB)**: catalog refresh/version/cache/failure behavior; annotation merge; version-fingerprint stability; bounded history; stale-serve on refresh failure; `SCH.UNAVAILABLE` with no cache.
2. **Introspection adapter**: against an injected `connect_fn` fake (the `DatabaseConnection.__init__` pattern) — table discovery, column discovery incl. types/nullability, PK/FK discovery, deterministic ordering, error mapping to typed errors.
3. **Determinism**: same metadata in different row orders → identical fingerprint/serialization (the §12 rules, executable).
4. **Drift**: each change class from the audit matrix (new/removed/renamed column, type, nullability, new table, relationship) ⇒ new version id; unchanged content ⇒ identical id.
5. **Contract tests**: implementation satisfies `contracts.SchemaCatalog`/`SchemaRetriever` (runtime_checkable isinstance **and signature parity** — the Sprint 3 pattern); additive-contract compatibility (old constructions still valid; `columns` ↔ `column_details` invariant).
6. **Architecture/boundary tests** (extend `tests/test_architecture/`): `src/core/schema/**` must not import `pymssql`, `src.core.llm.*`, provider SDKs, `src.agents.state`, `src.api/*`, `src.memory`; only `src/core/database/{connection,metadata}.py` may import `pymssql`; import-footprint probes (fresh subprocess) for `schema.catalog`; no import-time side effects (no DB connection, no cache writes at import — audit-hook pattern from 3.0C).
7. **Fixture-based**: a checked-in fake metadata fixture representing the audited 3-table schema drives all of the above deterministically.
8. **Wiring (3.1C)**: orchestrator path with injected catalog+retriever producing `schema_context` end-to-end on fakes.

No live database is required for any of it.

## 17. Trade-offs Accepted

* **Annotations are a second input** the catalog must merge — accepted because relationships/semantics are *not discoverable* from this database (no constraints **[S0]**); provenance fields make the merge honest.
* **Startup refresh** keeps the system honest by default at the cost of one cheap probe per process start — accepted (replaces an existing startup DB call of the same shape; net-zero cost).
* **Bounded version history on disk** adds a small file-management duty — accepted for `SCH.DRIFT`/`VERSION_NOT_FOUND` semantics the contracts already promise.
* **Table-qualified labels** break bare-key equivalence with `COLUMN_MAP` — accepted; a bare-name fallback accessor is specified for consumers during migration.

---

## 18. Implementation Status (Sprint 3.1B)

Implemented files (all new modules additive; legacy components untouched):

| Module | Content |
|---|---|
| `src/agents/contracts.py` | `ColumnDescriptor` (new); `TableDescriptor.column_details`/`.description`; `Relationship.kind`/`.cardinality`; `SchemaCatalog.refresh()` Protocol method — all additive defaults |
| `src/core/database/metadata.py` | `DatabaseMetadataReader` + driver-free VOs (`DiscoveredColumn`, `DiscoveredPrimaryKey`, `DiscoveredForeignKey`, `ProbeSignature`, `DatabaseMetadata`); constant introspection SQL; typed error mapping; **lazy** driver import |
| `src/core/schema/catalog.py` | `SchemaCatalog`: annotations loading (construction + re-check each refresh), probe-gated refresh, canonical merge with drift warnings, SHA-256 content fingerprint, canonical serialization/deserialization, content-addressed JSON cache, bounded history (default 5), stale-serve / `SCH.UNAVAILABLE` failure semantics, thread-safe via one instance lock |
| `src/agents/text_to_sql_agent.py` | additive composition: `self.schema_catalog = SchemaCatalog(DatabaseMetadataReader(**config))` — construction does local file reads only; **not consumed by the pipeline in this stage** |
| `data/schema/annotations.json` | seeded user-authored annotations (3 tables × 16 columns described, 48 table-qualified Persian labels, 2 annotated relationships `1:N`, 4 business-semantics entries; `mesc` label corrected to «کد ماده», stale `delivered_quantity` dropped, no Buyers fabrication) |
| `data/schema/schema_cache.json` | catalog cache (created on first refresh; the legacy `.schema_cache.json` is untouched) |

Exact behaviors:

* **Metadata discovery** (`metadata.py`): columns incl. types/nullability/ordinal via `INFORMATION_SCHEMA.COLUMNS`; PKs via `TABLE_CONSTRAINTS ⋈ KEY_COLUMN_USAGE`; FKs via `REFERENTIAL_CONSTRAINTS ⋈ KEY_COLUMN_USAGE` (composite keys → one row per column pair, matched by ordinal). System schemas excluded by a constant list (`sys`, `INFORMATION_SCHEMA`, `guest`, fixed database roles). Discovery is **not** scoped to the legacy three tables. Row statistics, indexes, defaults and comments are not discovered (absence is explicit, never fabricated).
* **Probe** (Phase 17): one aggregate query — `COUNT_BIG(*)` + `CHECKSUM_AGG(CHECKSUM(...))` over the same column metadata — combined with the annotations-file hash. Documented limitations, stated honestly: `CHECKSUM_AGG` has a remote collision risk, and the probe covers **columns only** — PK/FK/constraint drift is detected at the next full discovery (probe or `get_snapshot(version)` differences), never claimed otherwise. The post-discovery SHA-256 fingerprint remains the identity authority.
* **Refresh** (Phase 16): first access with a cache hit serves the snapshot without any database call; cache miss → full discovery; `refresh()` → probe-gated (skip when database probe **and** annotations hash both unchanged); `refresh(force=True)` → full discovery. Unchanged content reuses the existing version identity (same `captured_at`); changed content creates a new version. Failure with cache → stale-serve + `schema_refresh_failed(stale_served=true)`; failure without cache → `SchemaError` code `SCH.UNAVAILABLE` (fail-closed, no empty schema fabricated).
* **Cache** (Phase 14/15): `data/schema/schema_cache.json` — `{"cache_version": 1, "probe", "current", "snapshots": [...]}`; canonical JSON (sorted keys, compact separators, UTF-8, `ensure_ascii=False`); corruption → treated as miss + warning event + rebuild; write failure → logged, in-memory state unaffected; retention = newest `SCHEMA_HISTORY_SIZE` (5, centralized constant; per-instance override for tests), deterministic oldest-first pruning.
* **Telemetry** (Phase 18): `schema_refresh_started`, `schema_refresh_completed` (reason = `probe_unchanged|unchanged|changed`, version id, counts, duration, stale flag), `schema_refresh_failed` (error type, stale flag, duration), `schema_discovery_completed` (counts, duration), `schema_probe_completed`, `schema_annotation_drift` (warning count only), `schema_annotation_load_failed`, `schema_cache_load_failed`, `schema_cache_write_failed` — via the shared `log_event` surface; no raw SQL, no prompts, no row data, no credentials.
* **Determinism** (Phase 13): tables sorted by qualified name; columns by `(ordinal_position, name)`; relationships by `(from_table, from_column, to_table, to_column)`; semantics by `(applies_to, description)`; labels by key; fingerprint = SHA-256 over the canonical JSON of content only (version block excluded). Proven executable by row-order-invariance and annotation-change tests.

Boundary enforcement (executable, `tests/test_architecture/test_schema_boundary.py` + extended `test_boundary_hardening.py`): `src/core/schema/**` imports no driver/LLM/SDK/AgentState/API/UI/memory and only contracts + metadata adapter + `utils.logger`; `pymssql` sites = `connection.py` + `metadata.py` only; schema VOs defined only in `contracts.py`; fresh-subprocess footprint probes for `schema.catalog` and `database.metadata`; audit-hook import-time side-effect targets extended with both modules; catalog constructs offline.

Not implemented in 3.1B (by scope): `SchemaRetriever`, pipeline/orchestrator consumption, prompt integration, ColumnMapper retirement, `.schema_cache.json`/`schema_hash.py` removal.

---

## 19. SchemaRetriever (implemented in Prompt 3.1C)

`src/core/schema/retriever.py` — pure, deterministic, LLM-free, DB-free, state-free selection:

```
QuestionSpec + SchemaSnapshot  →  SchemaRetriever.retrieve()  →  SchemaContext
```

### Signals and weights (centralized in `RetrievalPolicy`)

| Signal | Table weight | Column weight |
|---|---|---|
| Qualified-name exact (`Sale.Orders` / `Sale.Orders.buyer`) | 10 | 10 |
| Exact unqualified name (`Orders` / `buyer`) | 6 | 6 |
| Case-insensitive name (only when exact did not fire) | 5 | 5 |
| Persian label equality (`SchemaSnapshot.labels`) | 4 | 4 |
| Description-token overlap | 3 (suppressed when the table was explicitly named — same evidence) | 3 |
| Business-semantics token (`applies_to` columns of matched entries) | — | 3 |
| Table owns a matched column (column score ≥ `min_column_score`) | +5 | — |

Limits: `min_table_score=4`, `min_column_score=3`, `table_top_k=3`, `column_top_k_per_table=6`, `max_join_path_edges=2`.

### Relationship assist and join paths

* One assist pass (never iterated): a below-threshold table gains `min(0.5 × strongest_neighbor_score, 6)` **only** when its strongest snapshot neighbor reached qualified strength (≥ 10) — i.e., the question explicitly named the neighbor. Weak textual evidence never cascades across the join graph; assisted tables rank strictly below their source.
* JoinHints come from bounded deterministic BFS over snapshot relationships only (undirected traversal, canonical direction preserved on the hint, sorted neighbor expansion, visited-set cycle prevention, shortest path per table pair, duplicate-edge elimination). Hints are planning metadata (`ColumnRef` pairs, `kind="inner"` default) — **never executable SQL**. Join-endpoint columns are always included in the context columns.

### Ambiguity and no-relevance semantics

* `SCH.AMBIGUOUS` warning iff the top table score is shared by ≥2 selected tables **none of which carries explicit table-name evidence**, or ≥2 tables are qualified-name-matched (conflicting strong evidence). A tie between explicitly named tables is ordinary multi-table relevance and never warns. Distinct evidence (e.g. the revenue-semantics link making `OrderItems.quantity` outrank `Shipments.quantity`) breaks ties deterministically.
* `SCH.NO_RELEVANT_SCHEMA` warning when no table reaches the threshold or the spec carries no signals. Both are context **warnings** per component-contracts §3; the retriever raises only typed `SchemaError` (`SCH.INTERNAL`) for `None` inputs.

### Determinism

Same `QuestionSpec` + `SchemaSnapshot` ⇒ identical `SchemaContext`: tables by (score desc, qualified name); columns by (score desc, ordinal, name); hints by canonical `(left.table, left.column, right.table, right.column)`; semantics preserve snapshot order (already canonical). No dictionary-order dependence, no floats in ordering keys, no randomness, no time.

### Wiring and dormancy status

* The façade constructs `self.schema_retriever = SchemaRetriever()` and passes `schema_catalog`/`schema_retriever` into `OrchestratorDeps` (the pre-existing seams).
* **The seam is DORMANT on the live path**: `_run_schema_resolution` requires `state.question_analysis is None → return state` — no context is fabricated, no transition, no catalog access. Proven by tests (`tests/test_schema/test_orchestrator_seam.py`).
* **Activation prerequisite**: a QuestionAnalyzer implementation that populates `state.question_analysis`. No hidden question-analysis behavior was implemented in 3.1C.
* Typed schema failures on the active path (`SCH.UNAVAILABLE`, retriever errors) are handled by the Orchestrator's known-failure path (`ErrorCategory.SCHEMA`, code preserved).
* One additive AgentState transition was required and approved-in-style: `SCHEMA_RESOLVED → SQL_GENERATED` (direct path, same documented pattern as the existing `INITIALIZED → SQL_GENERATED` transitional exception) so the seam can complete while no QueryPlanner is wired. Dormant on the live path.
* **Non-goal honored**: `FULL_SCHEMA`, prompt rules, few-shots, SQLExtractor and the whole SQL-generation path are untouched — `SchemaContext` is not yet consumed by generation.

---

## 20. Production Retrieval Boundary (implemented in Phase 3.2)

Phase 3.2 hardened the 3.1C retriever into a production retrieval boundary **without changing the live pipeline** (still Case B — see §20.8). All behavior below is exercised by injected `QuestionSpec`s in tests; nothing consumes retrieval on the live path yet.

### 20.1 Audit outcome (Phase 1 matrix)

| Stage | Status | Evidence |
|---|---|---|
| QuestionAnalyzer | **DORMANT** | No production implementation exists (grep-verified); `OrchestratorDeps.question_analyzer` is `None` on every live request |
| QuestionSpec production | **DORMANT** | `state.question_analysis` is always `None` on the live path |
| SchemaCatalog / snapshot | **WIRED, DORMANT** | Constructed at the façade; `get_snapshot()` never called on live requests (seam tests pin 0 reads) |
| SchemaRetriever | **WIRED, DORMANT** | Constructed at the façade; `retrieve()` requires a spec |
| SchemaContext | **DORMANT** | Produced only on the active test path; written solely by the Orchestrator via `with_schema_context` |
| Downstream consumer → LLM | **LEGACY (transitional)** | SQL generation goes through `SQLExtractor` → `sql_generation_prompt` → `FULL_SCHEMA` verbatim; `SchemaContext` does not reach any LLM call today |
| `FULL_SCHEMA` | **LEGACY — LIVE, documented** | Sole LLM-facing schema source (`src/core/prompts.py`); its import surface is pinned by `TestFullSchemaLeakagePrevention` (only `src/core/prompts.py` may import `src.core.database.models`) |

### 20.2 Query-type-aware evidence routing (Phase 8)

`QuestionSpec` fields are **not** equivalent evidence:

| Field | Surface routing | Rationale |
|---|---|---|
| `entities` typed `table` / `column` | routed to that surface only | explicit type |
| untyped `entities` | dual surface (tables + columns) | generic mention |
| `metrics` (names and `metric`-typed entities) | dual surface | a metric is a business *concept* that may legitimately appear in table descriptions and business semantics |
| `filters` (`field`) | **column-scoped only** (Phase 3.2 change) | a filter constrains a field; it is never evidence that a table was named or described |
| `time_constraints`, `references` | no contribution | no demonstrated deterministic mapping yet — documented QuestionAnalyzer prerequisite, not silently ignored |

Scoring weights remain centralized in `RetrievalPolicy` (§19); per-group weight *differentiation* beyond routing is deliberately deferred to the QuestionAnalyzer sprint (tuning weights without a real analyzer would be speculative).

### 20.3 Hard context bounds (Phase 9)

`RetrievalPolicy` now carries, in addition to the §19 limits: `max_join_hints` (12), `max_semantic_entries` (8) and `max_context_chars` (8000 — canonical serialized size, measured by the public `serialized_context_size()` with the catalog's JSON convention). The bound exists **before the LLM boundary** and never relies on prompt limits. No token estimate is computed: the repository has no token estimator and provider-specific token semantics must not be invented.

### 20.4 Deterministic context budgeting (Phase 10)

When the serialized content exceeds `max_context_chars` the retriever trims, in a fixed priority order — never randomly:

1. semantic entries beyond the cap (canonical snapshot order kept from the front);
2. remaining semantics in reverse canonical order, then
3. non-endpoint columns lowest-score-first (ties: lexicographically first table, then highest ordinal, then lexicographically first column name).

Never removed: selected tables, join hints, join-endpoint columns, warnings. A relationship is never truncated leaving one endpoint unexplained, and a hint is never split from its endpoint columns. The budgeted quantity is the context content; each trimming iteration measures the full candidate *including* the notice it will carry, so the delivered context respects the budget whenever the structural floor allows. If even the floor (tables + hints + endpoints + warnings) exceeds the budget, the floor is kept and the breach is reported — never silently unbounded. Any trimming is surfaced as a `SCH.CONTEXT_BOUNDED` warning (registered in `interface-design.md` §7.4) whose message reports the content size, the budget and what was dropped.

### 20.5 Semantic retrieval (Phase 7)

Tokenization is Unicode word-token casefolding (`\w+`, `re.UNICODE`) — Persian included; labels are compared as exact strings. There is **no stopword list** (metadata text is small and curated), no external NLP dependency, no embeddings (explicit non-goal). Ambiguity handling stays as §19 (score-shape rules).

### 20.6 Confidence and fallback (Phase 14)

`confidence = min(1, best_table_score / 10)` (rounded to 4 places). High-confidence contexts carry no warnings; moderate confidence surfaces the selected context with its approved warnings preserved; `SCH.AMBIGUOUS` keeps its §19 semantics; no relevant schema produces an empty context (`confidence=0.0`) with `SCH.NO_RELEVANT_SCHEMA`. **There is no fallback to the full snapshot anywhere in the retriever or the Orchestrator seam**: `NO_RELEVANT_SCHEMA ≠ SEND_FULL_SCHEMA` (pinned by tests).

### 20.7 Telemetry and runtime metadata (Phases 15-16)

* The retriever emits **one** `schema_retrieval_completed` event per retrieval (the component-contract §3 surface): outcome (`completed` / `no_match` / `ambiguous`), counts (tables, columns, hints, semantics), `context_chars`, confidence, `bounded` flag, snapshot version id, warning **codes** and duration. Payloads never contain the user question, prompt text, schema text or row data.
* The Orchestrator seam records `RuntimeMetadata.schema_metrics` (counts + `context_chars`) and a `schema_resolution` stage timing **only when retrieval runs** — additive fields on the existing runtime-metadata model (no second telemetry model), written only by the Orchestrator; empty on the live path while the seam is dormant.

### 20.8 Activation status (Phases 2, 24)

**Case B re-confirmed**: no production `QuestionAnalyzer` exists, so question-driven retrieval stays dormant on the live path exactly as in 3.1C (proven by the dormant-seam tests). The retrieval boundary is production-ready for the moment a spec exists: bounded, budgeted, deterministic, telemetry-instrumented, with the retrieval metrics of §20.10.

### 20.9 Schema rendering boundary (Phases 12-13)

`src/core/schema/renderer.py::SchemaContextRenderer` is the controlled LLM-facing rendering boundary:

```
SchemaContext  →  SchemaContextRenderer.render()  →  LLM schema payload
```

It is formatting, not retrieval: it accepts only a `SchemaContext` (typed rejection otherwise), imports nothing but the contract value objects, never queries the database/catalog/QuestionAnalyzer/LLMRouter, never invents content (every rendered element comes verbatim from the context), preserves join direction (`left = right` verbatim), is deterministic, and enforces a hard `max_chars` limit with a documented section-drop priority (semantics → notes → per-table columns beyond join endpoints); failing that, the structural floor cannot fit → typed `SchemaError` (fail-closed). The renderer is **dormant infrastructure** until the separately-authorized prompt-migration stage wires it in.

### 20.10 `FULL_SCHEMA` transitional status, leakage prevention and evaluation (Phases 11-12, 18-19)

* `FULL_SCHEMA` is **not removed** (its retirement requires measured retrieval parity + SQL-generation regression + explicit authorization). Its exact role: the legacy prompt-truth embedded by `src/core/prompts.py` into every SQL-generation prompt. The dual-schema path is documented here and in the renderer docstring.
* Executable leakage prevention: only `src/core/prompts.py` may import `src.core.database.models` (whole-tree AST test); the retriever, renderer and Orchestrator must never reference `FULL_SCHEMA`; the renderer rejects non-context input; the runtime seam test proves the SQLGenerator receives the retrieved `SchemaContext` (never the snapshot, never legacy text) and that unselected snapshot tables never reach the generation boundary.
* Evaluation: `tests/test_schema/fixtures/retrieval_cases.json` — 15 exact-expectation cases (single/two/three-table, quantity and Persian-label ambiguity, revenue semantics, Jalali filtering, irrelevant/weak matches, dangling references, no-signal) run against the real catalog-built snapshot; dataset metrics: table/column precision & recall = 1.0, irrelevant rates = 0.0, bounded-context compliance = 100%, **full-schema leakage rate = 0%** over the protected rendering path.
* Determinism/concurrency/performance: identical output across `PYTHONHASHSEED` values, label-dict orderings and repeated/concurrent runs; retrieval on a 200-table × 50-column synthetic snapshot completes well under the generous test bound while the context stays policy-bounded — retrieval cost scales with schema metadata, payload size with the bounded context.
