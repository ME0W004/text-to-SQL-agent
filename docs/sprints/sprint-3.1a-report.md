# Sprint 3.1 / Prompt 3.1A Report — Schema Audit & Canonical Schema Design

**Date**: 2026-09-04 · **Branch**: `new-ar` · **Stage**: 3.1A (audit/design ONLY — no production code changed)
**Final status**: **PASS WITH DEBT** (§34; all debt is pre-existing and documented, none created by this stage)

---

## 1. Executive Summary

Stage 3.1A audited every source of schema knowledge in the repository and designed the canonical Schema Intelligence architecture for Sprint 3.1 — without implementing anything. Key verified findings: (a) schema truth is fragmented across 12 inventoried sources, of which the only authoritative one (live DB metadata) is introspected for just 3 tables' columns and used solely as a cache key; (b) the prompt's only schema source (`FULL_SCHEMA`) is provably incomplete (no Shipments table section) and partially false (claims a Buyers relationship that does not exist), while the database itself declares **no PK/FK constraints**, so every relationship is annotated knowledge; (c) Persian labels are LLM-generated per run, ambiguous (bare column-name keys), contain stale/wrong entries, and are currently dead imports; (d) the "schema hash" is an MD5 cache key, not identity, and drift detection is column-only (new tables, relationships and semantic changes are invisible). The target design — recorded in three new documents and ADR-017 — establishes a source-of-truth hierarchy (DB metadata → catalog → snapshot → retrieval → prompt), a content-fingerprint version identity, deterministic ordering rules, a metadata-adapter database boundary, an LLM-free structural path, additive contract extensions (`ColumnDescriptor`, `TableDescriptor.column_details/description`, `Relationship.kind/cardinality`, `SchemaCatalog.refresh()`), and a staged migration that never touches the live pipeline. Full regression: **849 passed / 340 skipped / 0 failed** (exact 3.0C baseline match); compileall clean.

## 2. Current Schema Architecture

The live pipeline has **no schema component at all**: `AgentState.schema_context` stays `None` (the orchestrator's `_run_schema_resolution` stub applies an empty `SchemaContext()` only when a QuestionSpec exists, which never happens on the current path). What exists instead:

* **Prompt-embedded schema**: `FULL_SCHEMA` (hand-authored, `models.py:81-125`) injected verbatim into every SQL-generation prompt (`prompts.py:19`), plus 16 hand-authored rules (join paths, revenue formula, Jalali conventions) and 5 few-shots.
* **Static models**: `ORDERS_MODEL`/`ORDER_ITEMS_MODEL`/`SHIPMENTS_MODEL`/`ALL_MODELS` + `build_compact_schema()` — all verified dead (zero consumers).
* **Label pipeline**: façade construction → schema-hash check → (on change) subprocess → LLM-translated `column_mapper.py`; the resulting `COLUMN_MAP` is imported by the façade and `src/core/__init__.py` but never read (dead import since Sprint 2B removed `_filter_and_translate_rows`).
* **Contracts ready, unused**: `SchemaSnapshot`/`SchemaCatalog`/`SchemaRetriever`/`SchemaContext` VOs + Protocols exist in `contracts.py`, are tested, and the Orchestrator wiring seam exists.

## 3. Schema Sources Inventory

Twelve sources identified, classified and evidenced (full table with authority/freshness/consumers/reliability: [`docs/audit/schema-intelligence-current-state.md`](../audit/schema-intelligence-current-state.md) §2). Summary classification:

| Class | Sources |
|---|---|
| Canonical (partial) | live `INFORMATION_SCHEMA.COLUMNS` reads (columns of the 3 hardcoded tables only) |
| Legacy (dead) | `models.py` dicts, `build_compact_schema()`, empty `src/schemas/`, stray `scripts/src/` copy |
| Legacy / Prompt-only | `FULL_SCHEMA`, prompt rules, few-shots |
| Legacy / Presentation-only (dead) | `COLUMN_MAP` (+ its LLM generator pipeline) |
| Cache | `.schema_cache.json` (MD5), `schema_hash.py` |
| Test-only | `evaluation/baseline_questions.json` grounding facts |
| Documentation | Sprint 0 `database-profile.md` / `database-semantics-audit.md` (documented live inspection) |

No source was classed canonical merely for its filename; `models.py` is dead, and the only live introspection is scope-limited and consumer-less.

## 4. Verified Database Metadata Capabilities

Repository code can discover **today** (code inspection; no live connection opened — design-only stage):

| Capability | Present | Evidence |
|---|---|---|
| Column names/types/nullability (3 tables, hardcoded scope) | Yes | `schema_hash.py:15-25`, `generate_column_map.py:12-37` |
| Table discovery (unscoped) | **No** — table list hardcoded | same queries' `WHERE` clauses |
| Primary keys / foreign keys / constraints | **No** | no `sys.key_constraints`/`sys.foreign_keys`/`TABLE_CONSTRAINTS` query exists repo-wide |
| Indexes / defaults / comments / row statistics | **No** | absent (grep-verified) |

Critical structural fact (documented Sprint 0 inspection, `database-profile.md` §2 — cited, not re-observed): the DB declares **no PK and no FK constraints**; relationships exist only logically. Consequence: relationship metadata must be annotated, with provenance, until constraints exist. Hardcoded today: table universe (3 tables), schema name, all relationships, all semantics.

## 5. Current Table/Column/Relationship Inventory

Evidence-backed inventory for `Sale.Orders` (16 columns), `Sale.OrderItems` (16), `Sale.Shipments` (16+) with per-column type/nullability/key-role/default/semantics — full tables in the audit doc §4, cross-checked between `models.py` column lists (match the documented DB exactly **[S0]**) and `FULL_SCHEMA` descriptions. Highlights: `Shipments.Id` is **not unique** (795 duplicate groups **[S0]**); `buyerId`/`destinationId`/`orderTypeId`/`driverId`/`typeId` are dangling references; `orderNo` is a document number that does **not** join to `Orders.Id` **[S0]**; `discount` always 0.00. `UNKNOWN` is used literally where the repository provides no evidence (most column defaults). Relationships: exactly two explicitly documented (OrderItems.orderId→Orders.Id; Shipments.orderItemId→OrderItems.Id), both 1:N, both **constraint-free in the DB**; the `FULL_SCHEMA` claim of a Buyers table is false and excluded.

## 6. Semantic Metadata Audit

Semantic knowledge lives in four places today, none machine-consumable and none consistent-checked: `FULL_SCHEMA` glosses (revenue formula, Jalali conventions, status/product meanings), prompt rules 10/16, few-shot patterns, and the LLM-translated labels. One live contradiction proves the absence of a consistency mechanism: `FULL_SCHEMA` says `mesc` = material code; `COLUMN_MAP` translates it «مقدار» (quantity). Boundary decision (not merged in this stage): column/table-scoped meaning → `SchemaCatalog` (descriptions + `BusinessSemantics`); Persian display labels → `SchemaSnapshot.labels` (presentation); term vocabulary ("buyer" = customer) → future QuestionAnalyzer layer; SQL conventions (TOP/HAVING/LEFT JOIN/count-vs-sum) → future planner/generator policy — **not** catalog content.

## 7. Current Schema Versioning

Actual behavior (audit doc §7): MD5 of `TABLE.COLUMN.TYPE.NULLABLE` rows for 3 tables, computed at every façade construction, compared against `.schema_cache.json` (`{"schema_hash": "2227c4b73127620821e927d1a6b56692"}`); on mismatch a subprocess regenerates `column_mapper.py` via chunked LLM calls (identity fallback per chunk on failure). What is hashed: columns of 3 tables. What is cached/invalidated/regenerated: one hash / nothing / only the label map. Fail-open (construction continues on any error). Conclusion: a **column-map cache invalidator**, not schema versioning — no identity, no history, no consumer beyond regeneration.

## 8. Schema Drift Analysis

| Change | Detected? | Mechanism | Consequence | Reliable? |
|---|---|---|---|---|
| new / removed / renamed column (3 tables) | Yes | hash mismatch → map regen | prompt schema stays stale | Partial |
| datatype / nullability change | Yes | hash fields | triggers regen, but the regenerated artifact carries no type info → no-op for consumers | Detected, useless |
| new table (outside the 3) / removed table | **No** | hash, generator, prompt rule 11 all scoped to 3 tables | invisible system-wide | No |
| relationship change | **No** | nothing reads/hashable relationships | silent wrong joins | No |
| semantic description change | **No** | hand-authored text | wrong grounding persists | No |
| label semantic change | **No** | regen keyed to structural hash only | stale labels persist | No |

## 9. Current Prompt Schema Analysis

Measured by importing the real modules (static; no LLM, no DB):

| Quantity | Value |
|---|---|
| `FULL_SCHEMA` | 1,946 chars / 266 words / 44 lines; describes **2 of 3** tables; 32 of 48 columns |
| Full `sql_generation_prompt` (no history / with 2-msg history) | 6,048 / 6,286 chars (schema ≈ 32%) |
| Few-shots | 5 (all Orders⋈OrderItems; one filters year '1403' — outside the 1394–1399 data range **[S0]**) |
| Error-correction prompt | 271 chars template, **schema-blind** (no schema re-supplied) |
| Estimated token footprint | ~1,500–1,800 tokens/call (≈4 chars/token mixed EN/FA — estimate, method stated) |
| Ordering / redundancy | static text; relationships appear 3× in the prompt; revenue formula 3×; full payload on every request regardless of question |

## 10. Schema Duplication Findings

Six information classes exist in multiple independent copies (full table with per-copy evidence: audit doc §10): table/column names ×6 copies (one live, five hand-authored or dead); relationships ×4 (all hand-authored); business semantics ×3; Persian labels ×1 (dead); types/nullability ×1 (unconsumed); change signal ×1 (cache-only). In every case the canonical target under the design is singular, and derivations must be generated from it, never hand-maintained beside it.

## 11. Target Schema Intelligence Architecture

Full design: [`docs/architecture/schema-intelligence.md`](../architecture/schema-intelligence.md). Essence:

```
SQL Server system metadata ──(metadata adapter, refresh-time admin connection)──►
SchemaCatalog (owns refresh/cache/versioning; merges user-authored annotations) ──►
SchemaSnapshot (immutable, content-fingerprint versioned) ──►
SchemaRetriever (QuestionSpec+Snapshot → SchemaContext) ──►
AgentState.schema_context (Orchestrator-written) ──► prompt rendering (derived)
```

Placement: `src/core/schema/{catalog,retriever}.py` + `src/core/database/metadata.py` (the only new driver site). Catalog deps: metadata adapter + annotations file + cache + `log_event`; forbidden: LLMRouter, QueryExecutor, MemoryManager, AgentState (per existing contracts).

## 12. Canonical SchemaSnapshot Design

Existing shape kept; every field re-justified (architecture doc §5): `version` (identity+provenance), `tables` (extended descriptor), `relationships` (extended), `business_semantics` (kept), `labels` (kept, **re-keyed to table-qualified names** — bare keys collide across 3 tables for `Id`/`quantity`/`status`/`entityDate`/`serialNo`/`unit`/`description`/`packCount`/`palletCount`). Explicitly rejected: row statistics, indexes (none exist **[S0]**), sensitivity classification (no requirement today; documented future-compatible extension), precomputed retrieval fields (no demonstrated consumer).

## 13. Table / Column / Relationship Models

* **`TableDescriptor`** — additive extension: `column_details: tuple[ColumnDescriptor, ...] = ()` and `description: Optional[str] = None`; existing `name` (qualified)/`columns`/`primary_key`/`foreign_keys` unchanged; catalog invariant `names(column_details) == columns`.
* **`ColumnDescriptor`** (new VO) — `name`, `data_type`, `nullable`, `key_role` (`none|pk|fk`), `description`. Evaluated-and-rejected: `default` (no consumer), `aliases` (question-vocabulary concern), `sensitivity` (future extension point only).
* **`Relationship`** — additive extension: `kind` (`database_constraint|annotated|inferred`, default `annotated` — mandatory honesty because the DB declares no constraints) and `cardinality: Optional[str]` (`"1:N"` where documented). No relationships inferred from column names; only the two documented ones are modeled.

## 14. Business Semantics Model

`BusinessSemantics` (existing: `description`/`formula`/`applies_to`) is sufficient for schema-tied facts (revenue formula, Jalali conventions, status-value meanings, unit semantics). Lightweight — no ontology/knowledge-graph infrastructure. Term-level business vocabulary (aliases like "buyer"=customer) is explicitly kept **out** of the catalog and deferred to the future QuestionAnalyzer vocabulary; nothing is merged in this stage.

## 15. ColumnMapper Boundary Decision

`ColumnMapper` is **legacy mixed responsibility that decomposes into**: (a) *presentation metadata* — Persian display labels → migrate to `SchemaSnapshot.labels` with table-qualified keys (the contract already designates labels as presentation, consumed by `PresentationPlanner` per dependency-rules §3.12); (b) *nothing semantic* — its only pseudo-semantic content (the `mesc` label) is wrong. Schema meaning and Persian display labels will **not** become the same concept; ColumnMapper is not refactored in 3.1A/3.1B — it keeps operating unchanged and is deprecated once the catalog serves labels.

## 16. Schema Versioning Strategy

Version identity = **sha256 content fingerprint** over the snapshot's canonical serialization (structural + annotated content; `captured_at` excluded). Same content ⇒ same id regardless of dictionary/driver/process ordering. Content change ⇒ new version; bounded on-disk history enables `get_snapshot(version)` and the already-registered `SCH.DRIFT`/`SCH.VERSION_NOT_FOUND` semantics. The legacy MD5 stays exactly what it is — a column-map cache key — until that pipeline retires; new code never uses MD5. Rejected alternatives: timestamp identity, monotonic counters, migration versions (no migration tooling exists here).

## 17. Source-of-Truth Hierarchy

| Class | Content | Owner |
|---|---|---|
| Authoritative (structural) | tables, columns, types, nullability, key roles | SQL Server metadata via adapter |
| User-authored (semantic) | descriptions, formulas, labels | reviewed annotation file, versioned with the snapshot |
| Canonical runtime | merged, versioned snapshot | SchemaCatalog (single writer) |
| Derived | prompt text, retrieval indexes | computed from snapshots, never hand-maintained |
| Cached | serialized snapshot | catalog-owned local cache, rebuildable |
| LLM-generated at runtime | **nothing** | legacy generator deprecated; future LLM enrichment only via LLMRouter → reviewed annotations |

## 18. Refresh / Cache Strategy

Startup refresh with probe-then-discover (one cheap `INFORMATION_SCHEMA.COLUMNS` probe in steady state — net-zero vs today's startup hash call), full discovery only on change; explicit `refresh()`; hash-mismatch triggered refresh. **No scheduler.** This resolves Sprint-2 open question Q10 ("startup or admin-only") as *startup, bounded, plus explicit entry*. Failure: cached snapshot served stale-but-flagged; `SCH.UNAVAILABLE` only with no snapshot at all. Cache: local content-addressed JSON, catalog-owned, corruption-tolerant (treated as absent); **no Redis/external cache** — caching is justified (load-once/reuse/refresh-explicitly at 3.1A.28 scale) and already has an in-repo precedent.

## 19. SchemaRetriever Readiness

The snapshot supports future retrieval unmodified: filterable typed tables/columns; `Relationship` edges for join-path traversal; `BusinessSemantics.applies_to` for concept→column mapping; descriptions/labels as the Persian/English matching surface. Precomputed search text/normalized names/alias indexes deliberately excluded (3.1A.31) — computable by the retriever at process level; not snapshot fields until a consumer exists.

## 20. AgentState Interaction

Unchanged and explicit: `AgentState.schema_context` (existing field) is the **request-level snapshot of the selected context**, written only by the Orchestrator via the existing `with_schema_context` applier and `SCHEMA_RESOLVED` transition (both already implemented and tested). The persistent catalog and full snapshots **never** enter AgentState. Wiring seam already exists (`OrchestratorDeps.schema_catalog/schema_retriever`, `orchestrator.py:146-147,317-328`) — 3.1B needs no Orchestrator change.

## 21. Database Boundary

Structural discovery crosses the boundary through a new metadata adapter, **`src/core/database/metadata.py`** — the only new `pymssql` import site, next to `connection.py`. `src/core/schema/**` must not import the driver. This refines dependency-rules §3.2 (which pre-allowed the catalog a direct admin connection): same access right (read-only admin connection, refresh-time only, never the per-request execution path), cleaner direction — Domain/Application → SchemaCatalog contract → metadata adapter → SQL Server. Security Core untouched: introspection uses constant statements; user SQL never reaches the catalog; non-query execution stays disabled.

## 22. LLM Boundary

Structural schema discovery is fully deterministic — **no LLM anywhere in the schema path**. The one existing LLM-in-the-loop (the column-map generator, LLM-called at agent startup on drift) is deprecated by this design; its non-determinism is a documented defect. If semantic enrichment via LLM is ever added, it must go through `LLMRouter` (never provider SDKs) and produce reviewed annotation artifacts, never live truth. `src/core/schema/**` is boundary-test-forbidden from importing `src.core.llm.*` or any provider module.

## 23. Security / Sensitive Metadata Considerations

Catalog contents are metadata-only (no row data; introspection reads system catalogs). Exposure of table/column names and Persian labels is acceptable for this product's authorized users (consistent with component-contracts §2 "Medium" rating). Rules established: descriptions describe columns, never values (no PII in annotations); buyer/driver free-text name columns are documented as such in descriptions — recorded as facts, not as an invented classification system; a future `sensitivity` field on `ColumnDescriptor` is the documented extension point if a real requirement appears; cache files contain metadata only; log events carry counts/versions/durations only. No data-classification system was invented (explicit task constraint).

## 24. Determinism Rules

Same DB metadata + same annotations ⇒ byte-identical snapshot and fingerprint. Canonical ordering: tables by qualified name; columns by discovery `ORDINAL_POSITION`; relationships by `(from_table, from_column, to_table, to_column)`; semantics by `(applies_to, description)`; labels by key; JSON with sorted keys/fixed separators/UTF-8; `captured_at` excluded from fingerprint input. Design-only — rules become executable tests in 3.1B (§25).

## 25. Test Strategy (obligations for 3.1B)

Repository-native pytest only; no live DB required anywhere. Designed suites (architecture doc §16): unit tests on fakes (refresh/version/cache/failure/stale-serve); introspection-adapter tests via injected `connect_fn` (the existing `DatabaseConnection` pattern); determinism tests (row-order invariance ⇒ identical fingerprint); drift tests per change class; contract conformance (runtime_checkable **and signature parity** — Sprint 3 pattern); additive-compatibility tests; boundary tests (schema package driver/LLM/state/API/memory import bans; `pymssql` only in `database/{connection,metadata}.py`; canonical ownership of `ColumnDescriptor`); fresh-subprocess import-footprint and audit-hook side-effect probes (Sprint 3 pattern); checked-in 3-table fake-metadata fixture; wiring tests at 3.1C.

## 26. Package Placement

`src/core/schema/` (`catalog.py` in 3.1B; `retriever.py` in 3.1C) + `src/core/database/metadata.py`. Mirrors the approved ADR-016 `src/core/llm/` pattern. Rejected: new `src/domain|application/` packages (contradicts ADR-016 "no artificial layering"); `src/schemas/` (vestigial empty package); inside `database/` beyond the adapter (catalog is not an execution concern). All four boundary obligations (dependency direction, contract ownership, database boundary, LLM boundary, AgentState boundary) are respected and enforcement-designed.

## 27. Migration Strategy

Verdicts (nothing removed in 3.1A): `models.py` dicts + `build_compact_schema` — keep (dead) → deprecate → remove later; `FULL_SCHEMA` — keep (live prompt dependency) → content becomes reviewed catalog annotations → deprecate at 3.1C; column-map pipeline (`column_mapper*.py`, generator script, `.schema_cache.json`) — keep operating unchanged → deprecate when the catalog serves labels → remove later; `schema_hash.py` — keep (pipeline dependency) → retire with it; prompt rules/few-shots — keep until SQLGenerator lands. Migration invariant: the live pipeline (`/api/v1/ask`, CLI, evaluation) behaves identically at every step; the catalog is introduced additively.

## 28. Current vs Target Matrix

Full 10-row matrix in architecture doc §14 (and audit doc §10/§11). Covers: INFORMATION_SCHEMA reads, static dicts, compact builder, FULL_SCHEMA, prompt rules, few-shots, column-map pipeline, schema hash, `AgentState.schema_context`, orchestrator stub — each with today's responsibility, target owner, and strategy (discover/seed/derive/deprecate/keep/unchanged).

## 29. Documentation / ADR Changes

**Created** (all in this stage; documentation-only):
* `docs/audit/schema-intelligence-current-state.md` — evidence base (sources inventory, capabilities, per-column inventory, relationships, semantics, versioning, drift matrix, duplication, consumer analysis, discrepancies)
* `docs/architecture/schema-intelligence.md` — target design (hierarchy, placement, catalog/snapshot/descriptors, versioning, refresh/cache, boundaries, determinism, test strategy, migration, trade-offs)
* `docs/contracts/schema-intelligence.md` — contract reconciliation + additive refinements + enforcement design
* `docs/adr/ADR-017-canonical-schema-representation.md` — decision record (refines ADR-002 and dependency-rules §3.2; records the metadata-adapter refinement, fingerprint identity, refresh/cache decision, ColumnMapper boundary)

**Updated**: none (dependency-rules.md §3.2 refinement is recorded in ADR-017 and left to 3.1B implementation to apply — smallest-change discipline). ADR-002 remains accepted and is elaborated, not rewritten.

## 30. Open Questions

| # | Question | Disposition |
|---|---|---|
| Q1 | Exact annotation file format & location (e.g. `data/schema/annotations.json` schema) | decide at start of 3.1B (implementation detail; boundary + provenance rules already fixed) |
| Q2 | Version-history retention bound (N) and cache file path | 3.1B (small constants; contract unaffected) |
| Q3 | Should `SemanticValidator` (Sprint 4+) weight `kind="annotated"` relationships differently from `database_constraint`? | deferred to semantic-validation sprint; the `kind` field exists precisely to enable this |
| Q4 | Whether the label bare-name fallback accessor is a snapshot helper or a retriever utility | 3.1B/3.1C (behavior fixed; home is cosmetic) |
| Q5 | When the column-map pipeline is physically removed | a later, explicitly-authorized cleanup stage (not 3.1B) |

## 31. Risks

* **Annotation quality**: the catalog's semantic layer is only as good as the seeded annotations; mitigation — seed from `FULL_SCHEMA` + Sprint 0 documented semantics with human review in 3.1B, provenance fields preserve honesty.
* **Contract additive drift**: `columns` vs `column_details` duality could diverge; mitigation — catalog-enforced names-match invariant + executable test obligation.
* **Startup coupling**: catalog refresh at façade construction adds a failure surface at startup; mitigation — stale-serve on refresh failure (design §18), matching the existing fail-open hash-check behavior.
* **Two label regimes during migration** (dead `COLUMN_MAP` vs qualified snapshot labels); mitigation — column-map pipeline untouched until explicit retirement stage.
* **S0 evidence aging**: live-DB facts cite the Sprint 0 inspection; if the DB changed since, per-column details could differ — 3.1B's deterministic discovery makes this self-correcting at first refresh.

## 32. Verification Results

Design-only stage; the mandated commands were run against the untouched working tree:

| Command | Result |
|---|---|
| `uv run python -m pytest tests -q` | **849 passed, 340 skipped, 0 failed** (74.51 s) — exact match with the documented 3.0C baseline (849/340/0); skips are the known by-design protocol-matrix parametrization (`test_validation.py:261`) |
| `uv run python -m compileall -q src evaluation scripts tests` | **exit 0, clean** |

Tests passed: 849. Failed: 0. Skipped: 340 (all by-design, per the 3.0C skip audit; re-verified in kind by exact count match). No test was modified, added, hidden or skipped for this stage. Baseline did not differ unexpectedly.

## 33. Deviations

* **No deviation from the approved Sprint 2/3 architecture** was required; the design preserves every approved invariant (contracts authority, state single-writer, database boundary, LLM boundary, package rules) and refines only what the 3.1A task explicitly asked to be designed (dependency-rules §3.2 adapter refinement is recorded as a refinement in ADR-017, not a contradiction).
* **Documentation discrepancy recorded (not a design deviation)**: `docs/implementation/sprint-3a-report.md`, listed in the 3.1A task's authoritative documents, **does not exist**; the 3.0 stage record is `docs/sprints/sprint-3-prompt-3.0c-report.md` (discrepancy already recorded in the 3.0C report §28-D2). No content was invented to fill it.
* Design-stage constraint honored: no live database connection was opened; live-DB facts cite the documented Sprint 0 inspection and are marked **[S0]** throughout.

## 34. Final Status

**PASS WITH DEBT** — all Definition-of-Done items complete (§1–§33 above and the three design documents); the only "debt" is pre-existing, now precisely documented migration debt (static/dead schema artifacts and the column-map pipeline, §27), which this stage was explicitly forbidden to remove.

| Question | Answer |
|---|---|
| Runtime behavior changed | **NO** |
| Production code changed | **NO** (only documentation artifacts created) |
| Database behavior changed | **NO** |
| Security behavior changed | **NO** |
| AgentState behavior changed | **NO** |
| LLM behavior changed | **NO** |

## 35. Recommendation for 3.1B — SchemaCatalog & Canonical Schema Implementation (plan only; not started)

1. **Contracts (additive)**: add `ColumnDescriptor`; extend `TableDescriptor` (`column_details`, `description`) and `Relationship` (`kind`, `cardinality`); add `refresh()` to the `SchemaCatalog` Protocol — per `docs/contracts/schema-intelligence.md`; update contract tests + additive-compatibility identity tests.
2. **Metadata adapter**: `src/core/database/metadata.py` — table/column/PK/FK discovery via injected `connect_fn`; driver-free typed row output; typed error mapping; constant introspection SQL only; extend the `pymssql`-site architecture test.
3. **Annotations**: define the annotation file format (Q1); seed content from `FULL_SCHEMA` + Sprint 0 documented semantics (reviewed; provenance recorded).
4. **Catalog**: `src/core/schema/catalog.py` — startup probe-then-discover, explicit `refresh()`, sha256 content fingerprint per the §24 ordering rules, content-addressed local cache, stale-serve failure behavior, bounded history, `log_event` events (`schema_refresh_started/completed/failed`).
5. **Tests**: the §25 suite — fakes only, no live DB; determinism/drift/contract/boundary/footprint probes; checked-in 3-table fixture.
6. **Wiring (no behavior change)**: construct the catalog at the façade composition point alongside existing infra; **do not** yet feed the pipeline — the current path stays byte-identical until 3.1C authorization.
7. **Docs**: update dependency-rules §3.2 note, package-architecture placement table, and the 3.1B report.

Explicitly out of 3.1B scope: SchemaRetriever, QuestionAnalyzer, prompt changes, ColumnMapper/pipeline removal, orchestrator behavior changes.

---

**STOP** — per the 3.1A final stop rule, this stage ends here. SchemaCatalog, SchemaRetriever, QuestionAnalyzer, 3.1B and 3.1C have **not** been started and await explicit authorization.
