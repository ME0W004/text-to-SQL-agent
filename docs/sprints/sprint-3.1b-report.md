# Sprint 3.1 / Prompt 3.1B Report — SchemaCatalog & Canonical Schema Implementation

**Date**: 2026-09-04 · **Branch**: `new-ar` · **Stage**: 3.1B (implementation stage, additive & non-disruptive)
**Final status**: **PASS** (§37; deviations are documented implementation refinements within the approved design, §34)

---

## 1. Executive Summary

Stage 3.1B implemented the approved Sprint 3.1A Schema Intelligence foundation exactly as designed: additive contract extensions (`ColumnDescriptor`, `TableDescriptor.column_details/description`, `Relationship.kind/cardinality`, `SchemaCatalog.refresh()`); the second approved, lazily-importing driver site `src/core/database/metadata.py` (read-only introspection: tables/columns/types/nullability/PKs/FKs, driver-free typed output, constant SQL only); the seeded, reviewable annotation source `data/schema/annotations.json`; and `src/core/schema/catalog.py` — the canonical `SchemaCatalog` owning annotation merge with drift warnings, SHA-256 content fingerprinting, deterministic serialization, a content-addressed local cache, bounded history, probe-gated refresh, stale-serve fallback and fail-closed `SCH.UNAVAILABLE` semantics, plus structured telemetry. The catalog is composed additively at the façade and constructs offline; **nothing in the live request pipeline consumes it**, so runtime behavior is unchanged. Full regression: **923 passed / 356 skipped / 0 failed** vs baseline 849/340/0 — the exact arithmetic reconciliation is §33 — and compileall clean.

## 2. Baseline Before Implementation

Recorded at Phase 0, before any code change (working tree otherwise identical to the 3.1A end state):

| Command | Result |
|---|---|
| `uv run python -m pytest tests -q` | **849 passed, 340 skipped, 0 failed** (73.33 s) |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean |

Matches the 3.1A report's verification exactly. Skips are the by-design protocol-matrix parametrization (`test_validation.py::test_protocol_method_parameters`).

## 3. Implemented Files

**New production modules**
* `src/core/database/metadata.py` — metadata adapter (lazy driver import, driver-free VOs, constant SQL, typed error mapping)
* `src/core/schema/__init__.py`, `src/core/schema/catalog.py` — canonical `SchemaCatalog`

**New artifacts**
* `data/schema/annotations.json` — seeded user-authored semantic annotations (checked-in, reviewed)
* (`data/schema/schema_cache.json` is created on first refresh — nothing triggers it in this stage, so it does not exist yet)

**New tests**
* `tests/test_schema/conftest.py`, `tests/test_schema/fixtures/three_table_metadata.json`, `tests/test_schema/test_metadata_adapter.py`, `tests/test_schema/test_catalog.py`
* `tests/test_architecture/test_schema_boundary.py`

**Modified production files (additive only)**
* `src/agents/contracts.py` — schema contract extensions (§4–§7)
* `src/agents/text_to_sql_agent.py` — additive catalog composition (§25)

**Modified tests**
* `tests/test_architecture/test_validation.py` — `SchemaCatalog` matrix entry gains `"refresh": []` (documented mechanism: dependency-rules §10 "new components must add the corresponding test entries")
* `tests/test_architecture/test_boundary_hardening.py` — pymssql allowed-sites list gains `metadata.py`; `SIDE_EFFECT_TARGETS` gains `src.core.database.metadata` and `src.core.schema.catalog`

**Documentation**
* `docs/architecture/schema-intelligence.md` (§18 implementation status), `docs/contracts/schema-intelligence.md` (§11 exact annotation/cache schema), `docs/architecture/dependency-rules.md` (§3.2 refinement), `docs/architecture/package-architecture.md` (inventory + target tree), `docs/adr/ADR-017` (status → Accepted; "Implementation status" section)

## 4. Contract Changes

All in `src/agents/contracts.py`, all additive frozen-dataclass defaults; `__all__` extended with `ColumnDescriptor`. The protocol matrix (`test_validation.py`) gained 17 parametrized pairs — 1 real check (`SchemaCatalog.refresh`) + 16 by-design skips, which is the whole skip delta (340 → 356). `runtime_checkable` behavior preserved; no circular imports (verified by the whole-tree cycle test in the full run); contract authority unchanged.

## 5. ColumnDescriptor

```python
@dataclass(frozen=True)
class ColumnDescriptor:
    name: str
    data_type: str
    nullable: bool
    key_role: str = "none"          # "none" | "pk" | "fk"
    description: Optional[str] = None
```

Immutable (freeze pinned by test), typed, equality-safe, driver/LLM/state-free. Exactly the five approved fields — no `aliases`/`sensitivity`/`default`/statistics/retrieval fields (explicit task constraint). Single canonical definition, scan-enforced.

## 6. TableDescriptor Changes

Additive: `column_details: tuple[ColumnDescriptor, ...] = ()`, `description: Optional[str] = None`. Existing fields and constructors unchanged (`test_additive_contract_compatibility` pins old-style construction). Invariant `names(column_details) == columns` holds by construction in the catalog and is asserted per table in the suite.

## 7. Relationship Changes

Additive: `kind: str = "annotated"` (`"database_constraint" | "annotated" | "inferred"`), `cardinality: Optional[str] = None`. Seeded relationships carry `cardinality="1:N"` and `kind="annotated"` (the database declares no constraints). No inference from names/types/suffixes anywhere — pinned by `test_no_inferred_relationships_from_similar_names` (same-named columns across tables produce no relationships).

## 8. Metadata Adapter

`src/core/database/metadata.py` (`DatabaseMetadataReader`):
* injectable `connect_fn` (the established `DatabaseConnection` pattern); connection opened per read, closed on every exit path (success and failure — tested);
* execution timeouts from the existing `ExecutionPolicy` (`query_timeout`/`connection_timeout`);
* errors mapped to `DatabaseError`/`DatabaseErrorCode` mirroring `connection.py`'s timeout/permission/generic classification (implemented locally rather than calling the private `_map_error` — documented reuse-by-convention);
* driver import is **lazy** (inside the constructor when no `connect_fn` is injected — ADR-016 lazy-provider pattern), so importing the module loads no driver.

## 9. Metadata Discovery Queries

Four module-level constant statements, never parameterized (asserted by test):

1. **Probe** — `SELECT COUNT_BIG(*), CHECKSUM_AGG(CHECKSUM(TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE)) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA NOT IN (...)`
2. **Columns** — `SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA NOT IN (...) ORDER BY ...`
3. **Primary keys** — `TABLE_CONSTRAINTS ⋈ KEY_COLUMN_USAGE` where `CONSTRAINT_TYPE = 'PRIMARY KEY'` (composite PKs → one row per column, ordered by key ordinal)
4. **Foreign keys** — `REFERENTIAL_CONSTRAINTS ⋈ KEY_COLUMN_USAGE` (self-join on ordinal positions; composite FKs → one row per column pair)

System schemas excluded by a constant list (`sys`, `INFORMATION_SCHEMA`, `guest`, fixed database roles). Discovery is **broader than the legacy three-table scope** — every non-system schema visible to the login. Row statistics, indexes, defaults, comments: not discovered; absence is explicit (never fabricated). `IS_NULLABLE` normalized to `bool`; ordinals to `int`.

## 10. Driver Boundary

`pymssql` import sites repo-wide: `src/core/database/connection.py` (pre-existing) and `src/core/database/metadata.py` (new, lazy) — whole-tree AST scan updated and passing. `src/core/schema/**` imports zero driver modules (AST scan + fresh-subprocess probes). No second execution abstraction exists: the adapter is introspection-only, has no `execute_query` surface, and never touches the per-request path.

## 11. Annotation File

`data/schema/annotations.json` — UTF-8, pretty-printed (`ensure_ascii=False`), human-reviewable, checked in. Not LLM-generated (task constraint); seeded from approved repository evidence only.

## 12. Annotation Schema

```json
{
  "annotation_version": 1,
  "source": "<provenance string>",
  "tables": {"<schema>.<table>": {"description": "...", "columns": {"<col>": {"description": "..."}}}},
  "relationships": [{"from_table": "...", "from_column": "...", "to_table": "...", "to_column": "...", "cardinality": "1:N"}],
  "business_semantics": [{"description": "...", "formula": "...", "applies_to": ["<schema>.<table>.<column>"]}],
  "labels": {"<schema>.<table>.<column>": "<Persian label>"}
}
```

Structural fields are never taken from this file; the file hash participates in the refresh probe, so annotation edits create new versions.

## 13. Seeded Annotations

* **3 tables described**, all 48 columns described (Orders/OrderItems from `FULL_SCHEMA` glosses; Shipments from the Sprint 0 documented inspection — closing the `FULL_SCHEMA` Shipments gap at the annotation layer).
* **48 table-qualified Persian labels** seeded from legacy `COLUMN_MAP`, qualified per table. Two documented corrections, not silently copied: `mesc` label corrected to «کد ماده» (the legacy «مقدار» is proven wrong); stale `delivered_quantity` (no such column) dropped.
* **2 annotated relationships** (`1:N`): OrderItems.orderId→Orders.Id, Shipments.orderItemId→OrderItems.Id. **No Buyers table fabricated**; no dangling ID column (`buyerId`/`destinationId`/`orderTypeId`/`driverId`/`typeId`) turned into a relationship.
* **4 business-semantics entries** (revenue formula; Jalali year filtering; Jalali month grouping; delivered-vs-ordered quantity), each with `applies_to` provenance.
* Provenance recorded in the `source` field (evidence documents + corrections).

## 14. Canonical Merge Rules

Structural truth (adapter) always wins for existence, type, nullability, ordinal, declared keys; annotations fill descriptions/labels/semantics/logical relationships only. Stale annotations are **never silently discarded**: every annotation referencing an unknown table, unknown column, unknown relationship endpoint, or unknown semantics target is dropped **and** recorded (in `catalog.last_merge_warnings` + a `schema_annotation_drift` event with counts). Structural annotations cannot override DB types/nullability (the schema carries none — precedence is structural by construction, pinned by test).

## 15. SchemaCatalog

`src/core/schema/catalog.py::SchemaCatalog` — owns refresh, annotation loading (+ re-check per refresh), merge, snapshot construction, fingerprinting, cache, bounded history, stale serving, telemetry. Does **not** own question analysis, retrieval, generation, prompts, LLM routing, memory, execution or AgentState. Depends on: `contracts`, `utils.logger`, the duck-typed `MetadataReader` protocol (satisfied structurally by the adapter — no import coupling), local files. Thread-safe via one instance `RLock`; state is per-instance (tested).

## 16. SchemaSnapshot Construction

Built as the approved contract shape: `version` + `tables` (sorted by qualified name; `column_details` in ordinal order; `primary_key` = lowest-ordinal declared PK column or `None`; `foreign_keys` = out-edges) + `relationships` (DB constraints first-class, then non-duplicate annotated ones; sorted) + `business_semantics` (sorted by applies_to) + `labels` (table-qualified keys only). Label keys like `Sale.Orders.Id` / `Sale.Shipments.Id` are distinct per table (tested with the duplicate-name case); bare-column-only keys are never canonical.

## 17. Fingerprinting

SHA-256 hex over the canonical JSON of **content only** (tables incl. embedded FKs, relationships, business_semantics, labels). Excludes the entire version block (`captured_at`, `source`): identity is pure content, and the version block cannot hash itself. Same logical content ⇒ same fingerprint regardless of dictionary insertion order, metadata row order, process, host, driver iteration order (row-order-invariance and cross-instance tests). Content change (structural *or* annotated) ⇒ new fingerprint (tested for both).

## 18. Deterministic Serialization

Canonical JSON: `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, UTF-8. Ordering rules: tables by qualified name; columns by `(ordinal_position, name)`; relationships by `(from_table, from_column, to_table, to_column)`; semantics by `(applies_to, description)`; labels by key. Round-trip through the cache reproduces an equal `SchemaSnapshot` (tested).

## 19. Cache

`data/schema/schema_cache.json` (catalog-owned; **not** the legacy `.schema_cache.json`, which remains untouched with its pipeline): `{"cache_version": 1, "probe": {...}, "current": "<id>", "snapshots": [...]}`. Content-addressed (each snapshot keyed by its fingerprint); corruption/absence → treated as miss + warning event + rebuild on next refresh (tested with garbage bytes); write failure → logged, in-memory state unaffected; contains metadata/annotations only — no row data.

## 20. Snapshot History

Bounded by the centralized constant `SCHEMA_HISTORY_SIZE = 5` (per-instance override for tests); newest retained, deterministic oldest-first pruning; pruned versions map to `SCH.VERSION_NOT_FOUND` (tested with 5 refreshes at retention 3).

## 21. Refresh / Probe Behavior

* First access (`current_version`/`versions`/`get_snapshot`): cache hit → serve (zero database calls — asserted); miss → full discovery (a probe comparison is meaningless without a baseline).
* `refresh()`: probe-gated — one aggregate probe + annotations-file hash; both unchanged → reuse current snapshot (`reason="probe_unchanged"`, `read_metadata` not called — asserted); otherwise full discovery.
* `refresh(force=True)`: full discovery bypassing the gate.
* Unchanged content after discovery → same version identity, same `captured_at` (`reason="unchanged"`); changed content → new version (`reason="changed"`).
* Probe limitations documented honestly (§18 of the architecture doc): covers columns only; `CHECKSUM_AGG` collision risk; constraint drift is caught at the next full discovery; the fingerprint remains the identity authority.

## 22. Stale-Serve Behavior

Refresh failure with an existing current snapshot → that snapshot is served unchanged, `self._stale` set, `schema_refresh_failed` emitted with `stale_served=true`; a subsequent successful probe-unchanged/refresh clears the flag. Tested end-to-end (failure → recovery).

## 23. Failure Semantics

No snapshot available + refresh failure → typed `SchemaError` with code **`SCH.UNAVAILABLE`**, fail-closed, cause preserved; no empty schema fabricated (tested). Unknown version → `SCH.VERSION_NOT_FOUND` (tested). All codes pre-registered in `interface-design.md` §7.4 — the registry was not extended.

## 24. Telemetry

Shared `log_event` surface only, no second framework: `schema_refresh_started`, `schema_refresh_completed` (reason/version/counts/stale/duration), `schema_refresh_failed` (error type/stale/duration), `schema_probe_completed`, `schema_discovery_completed` (counts/duration), `schema_annotation_drift` (count only), `schema_annotation_load_failed`, `schema_cache_load_failed`, `schema_cache_write_failed`. Payloads carry counts/ids/durations — never raw SQL, prompts, row data, secrets or credentials.

## 25. Composition / Wiring

`TextToSQLAgent.__init__` additionally constructs `self.schema_catalog = SchemaCatalog(DatabaseMetadataReader(**config))` after the existing wiring. Construction performs local file reads only (annotations + cache) — no database I/O, no LLM, no network. **The catalog is not consumed by the request pipeline in this stage**: not passed into `OrchestratorDeps`, `AgentState.schema_context` untouched, prompts untouched, `SQLExtractor` untouched, `/api/v1/ask` byte-identical. All façade-legacy test-construction paths (e.g. `object.__new__` + attribute injection in `test_wiring.py`) keep working — the whole `tests/test_llm` suite passes unchanged.

## 26. AgentState Boundary

The catalog never imports, stores, or mutates `AgentState` (AST scan + import rules + fresh-subprocess state probe). `AgentState.schema_context` remains Orchestrator-owned request-level context; zero behavior change (state suite green).

## 27. LLM Boundary

`src/core/schema/**` imports no `src.core.llm.*`, no provider module, no SDK (AST scans; fresh-subprocess probes show no provider SDK and no LLM boundary layer loaded — `src.core.llm.policy`, the approved provider-neutral VO leaf reached transitively via `contracts`, is explicitly exempted in the probe, matching the ADR-016 pattern). No LLM call exists anywhere in refresh/merge/labels.

## 28. Security Boundary

Introspection is read-only, constant, never parameterized (asserted); no user SQL can reach the metadata layer (no such API exists); the per-request execution path, `SQLSecurityValidator` and `ExecutionPolicy` are untouched (security + database suites green); the adapter reuses the typed error model and closes connections on every exit path; cache/telemetry carry no sensitive content.

## 29. Test Coverage

**`tests/test_schema/` — 52 tests, all passing, no live database:**

| Group | Coverage |
|---|---|
| Metadata adapter (17) | column/table discovery + normalization, ordinal ordering, row-order independence, dedup, PK discovery, FK discovery incl. composite, empty metadata, probe (values + NULL checksum), constant-SQL/no-parameter assertion, timeout/permission/generic error mapping, connection close on success *and* failure |
| Catalog behavior (29) | first refresh from fixture; `names(column_details)==columns`; no fabricated PKs; qualified labels incl. duplicate column names; annotated-only relationships; no inferred relationships; descriptions/semantics merged; structural precedence; unchanged/changed/forced refresh; probe gating (call counters); annotations-change rediscovery; capture-time independence; stale serve + recovery; no-cache fail-closed; cache corruption/rebuild; cross-instance cache reuse without database; bounded history + pruning + version-not-found; fingerprint row-order/content/time invariance; annotation drift (unknown table/column/relationship) with warnings; missing annotations → structural-only; DB-constraint precedence over annotation; instance isolation; threaded refresh; protocol conformance; signature parity (all 4 methods); additive compatibility; VO frozen/equality |
| Fixture (checked-in) | 48 unique rows; 3 tables × ordinals 1–16 exactly; zero declared constraints (faithful to the documented DB); types/nullability per Sprint 0 profile; deliberately shuffled rows |

**`tests/test_architecture/test_schema_boundary.py` — 15 tests**: schema-package forbidden-import matrix (driver/LLM/SDK/state/adapters/facade/api/web/memory/fastapi/streamlit × each file); explicit no-driver rule; whole `src.*` import allowlist per file; approved pymssql sites; canonical ownership of all six schema VOs; fresh-subprocess footprint for `schema.catalog` and `database.metadata` (no SDK/driver/state/LLM-boundary module loaded); offline construction with an exploding reader.

**Extended existing suites**: protocol matrix (+`refresh`), whole-tree pymssql sites, side-effect targets (+2).

## 30. Architecture Enforcement

Executable in this stage: driver-site allowlist; schema-package import matrix; single canonical definition per schema VO (whole-tree AST); fresh-subprocess footprint probes; CPython audit-hook import-time side-effect targets for both new modules (no sockets/spawns/writes at import — part of the 59-test architecture run, passing); package cycle detection (whole-tree, covers the new `src.core.schema` package); signature-parity conformance. Contract doc §9's promised rules are now all test-backed.

## 31. Legacy Compatibility

Untouched and verified by the full regression: `models.py` (incl. `FULL_SCHEMA`, `build_compact_schema`), `column_mapper.py`, `column_mapper_updater.py`, `scripts/generate_column_map.py`, `.schema_cache.json`, `schema_hash.py`, the façade's startup column-map check, prompts, few-shots, extractor, orchestrator, memory, API, UI, evaluation. Their deprecation status is documented (architecture doc §14, dependency-rules, package-architecture inventory) but retirement is explicitly deferred.

## 32. Documentation Changes

All updated to describe **implemented** behavior: architecture doc §18 (exact paths, schema, cache, retention bound, probe + limitations, refresh semantics, telemetry, fingerprint algorithm, boundary tests); contracts doc §11 (exact annotation + cache schema); dependency-rules §3.2 (adapter refinement, enforced-by pointers); package-architecture (inventory rows + target tree); ADR-017 (Accepted + implementation refinements). Sprint 3.1A design statements that 3.1B refined are called out in §34 below.

## 33. Verification Results

| Command | Result | Δ vs baseline |
|---|---|---|
| `uv run python -m pytest tests -q` | **923 passed, 356 skipped, 0 failed** (86.01 s) | +74 passed, +16 skipped |
| `uv run python -m pytest tests/test_schema -q` | 52 passed | new |
| `uv run python -m pytest tests/test_architecture/test_schema_boundary.py tests/test_architecture/test_boundary_hardening.py -q` | 59 passed | — |
| `uv run python -m pytest tests/test_llm tests/test_agents tests/test_integration tests/test_evaluation tests/test_observability -q` | 332 passed | no regression |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean | — |

**Exact arithmetic reconciliation of the delta** (nothing hidden):
* +52 new `tests/test_schema` tests; +15 new `test_schema_boundary.py` tests; +1 protocol-matrix instance (`SchemaCatalog`×`refresh`); +6 side-effect-target instances (2 new targets × 3 parametrized probes) ⇒ **+74 passed** (849 → 923).
* +16 skips: adding `refresh` to the matrix's method-name list creates 17 new protocol×method pairs — 1 real check (SchemaCatalog) + 16 by-design non-member skips ⇒ **+16 skipped** (340 → 356).
* 0 failed; no test skipped, xfailed, or weakened to force a pass. The only existing-test modifications are the two documented extensions above (§3).

## 34. Deviations

No architectural deviation from the approved 3.1A design was required. Four implementation refinements, each within the design's stated intent, recorded in ADR-017 "Implementation status" and here:

| # | 3.1A design statement | 3.1B implementation | Reason | Compatibility impact |
|---|---|---|---|---|
| D1 | "startup refresh … at construction" | refresh trigger is **first access**, not `__init__`; construction = local file reads only | Phase 19 mandates zero request-pipeline change; nothing consumes the catalog in 3.1B, so no new runtime DB call exists anywhere; the refresh *flow* (cache → probe → discover) is exactly as designed | none (offline construction is a superset of the designed safety) |
| D2 | driver-import site `metadata.py` | driver imported **lazily** inside the reader constructor | ADR-016 lazy-provider pattern; keeps `import src.core.schema.catalog` driver-free (probe-pinned) | none (injected `connect_fn` behavior unchanged) |
| D3 | fingerprint over canonical serialization minus `captured_at` | fingerprint excludes the **whole version block** (`captured_at` + `source`) | the version block cannot hash itself; identity = pure content | none (ids remain stable across time/host, per design intent) |
| D4 | annotations loaded from file | annotations re-checked **at every refresh** (hash in probe signature) | makes annotation edits produce new versions as designed (runtime detection, not construction-only) | none |

One test-infrastructure note (not a deviation): `DatabaseMetadataReader` connection parameters are optional at construction because construction is I/O-free and the façade's test fixtures inject partial config (`{"host": "fake"}`); incomplete credentials surface only at read time as typed failures.

## 35. Remaining Debt

Pre-existing, unchanged, now documented with deprecation status (retirement requires explicit authorization):
* Column-map pipeline (`column_mapper.py`, `column_mapper_updater.py`, `generate_column_map.py`, `.schema_cache.json`, `schema_hash.py`) — superseded by catalog labels/versioning once wired.
* Static schema exports (`models.py` dicts, `build_compact_schema`) — dead, deprecated.
* `FULL_SCHEMA` prompt dependency — retires at SQLGenerator/SchemaRetriever wiring (3.1C).
* Task-bound `LLMClient` adapter at the extraction/answer seams; façade/orchestrator DB-driver composition import (A2); zero token usage until per-provider wiring (Sprint 3 debt).
* Stray `scripts/src/core/column_mapper.py` copy (repo hygiene).

## 36. Known Limitations

* The probe covers column metadata only; constraint/PK/FK drift is detected at the next full discovery (documented, not claimed otherwise). `CHECKSUM_AGG` has a remote collision risk.
* Composite PKs surface `TableDescriptor.primary_key` as the lowest-ordinal column (contract field is single-valued); all PK columns carry `key_role="pk"`. Composite FKs become one `Relationship` per column pair.
* Discovery scope = every non-system schema visible to the login; a deployment naming a business schema exactly like a system schema would be excluded (constant, reviewable list).
* The catalog instance caches snapshots in memory without external invalidation; multi-process deployments would each own their catalog state (single-process is the repository's model).
* `buyerId`-style dangling references remain unresolved by design; relationships are only the two documented ones plus any future declared constraints.

## 37. Final Status

**PASS** — all Definition-of-Done items verified: contracts implemented; metadata adapter with typed driver-free output broader than the legacy three-table hash; annotations externalized/reviewable/seeded without fabrications; deterministic merge with structural precedence and drift warnings; deterministic snapshot + SHA-256 identity (time-independent); content-addressed cache; bounded history; stale fallback; fail-closed `SCH.UNAVAILABLE`; version lookup with `SCH.VERSION_NOT_FOUND`; no inferred relationships; provenance preserved; table-qualified labels; no LLM dependency in the schema layer; no direct driver dependency from `src/core/schema`; no AgentState dependency; **no request-pipeline behavior changed** (849 pre-existing tests pass unchanged; delta fully reconciled); legacy pipeline intact; architecture/targeted/regression suites and compileall green; documentation matches implementation; this report complete.

| Question | Answer |
|---|---|
| Runtime behavior changed | **NO** (catalog composed but unconsumed; zero new runtime calls) |
| Public API changed | **NO** (`/api/v1/ask`, `AskRequest/AskResponse`, CLI, evaluation unchanged; contracts extended additively) |
| Security behavior changed | **NO** (Security Core/execution path untouched; introspection read-only constant SQL) |
| Database execution behavior changed | **NO** (existing execution untouched; adapter is a separate read-only introspection path, dormant until consumed) |
| AgentState ownership changed | **NO** (Orchestrator remains sole writer; catalog never touches state) |
| Observability behavior changed | **NO** (new schema events via the existing `log_event` surface; existing events untouched) |
| LLM behavior changed | **NO** (schema layer is LLM-free; provider boundary untouched) |

## 38. Recommendation for Sprint 3.1C (plan only — not started)

1. **SchemaRetriever** (`src/core/schema/retriever.py`): deterministic `retrieve(spec, snapshot) -> SchemaContext` — table/column scoring from `QuestionSpec` entities/filters/metrics against descriptions/labels/semantics; FK-derived `JoinHint`s; `SCH.NO_RELEVANT_SCHEMA`/`SCH.AMBIGUOUS` warning semantics per component-contracts §3; fakes-only tests.
2. **Orchestrator wiring**: inject `schema_catalog`/`schema_retriever` via the existing `OrchestratorDeps` seams; `_run_schema_resolution` becomes live (QuestionAnalyzer arrival is a prerequisite decision — without it, `question_analysis` is always `None` and resolution stays dormant).
3. **Startup refresh activation decision**: move the first-access refresh to an explicit façade-startup step once the pipeline consumes it (revisit D1).
4. **Prompt integration design** (separate approval): rendering `SchemaContext` instead of `FULL_SCHEMA`, with the seeded annotations as grounding; retire `FULL_SCHEMA` only after parity evaluation.
5. Documentation + 3.1C report per the established pattern.

---

**STOP** — per the 3.1B critical stop rule, this stage ends here. SchemaRetriever, QuestionAnalyzer, prompt migration, ColumnMapper retirement, Orchestrator schema wiring and runtime schema injection have **not** been started and require explicit authorization.
