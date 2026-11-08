# Schema Intelligence Contracts

**Sprint**: 3.1 — Prompt 3.1A (design) / **Prompt 3.1B (implementation)**
**Status**: **Implemented in Prompts 3.1B–3.1C** (§2–§6 below are live code in `src/agents/contracts.py`; §10 annotation schema implemented as specified; §8 AgentState interaction unchanged; implementation refinements recorded in ADR-017 "Implementation status")
**Authority note**: `src/agents/contracts.py` remains the single canonical contract surface (ADR-012, ADR-016). This document **reconciles** the existing schema-domain contracts with the target design ([`docs/architecture/schema-intelligence.md`](../architecture/schema-intelligence.md)); it defines **no duplicate types** anywhere. All changes below are **additive** (new optional fields with defaults) so every existing construction site — including `tests/test_agents/test_contracts.py` and `tests/test_architecture/test_validation.py` — remains valid.

---

## 1. Existing contracts — reconciliation verdict (3.1A.24)

| Contract object | Present in `contracts.py` | Verdict | Action in 3.1B |
|---|---|---|---|
| `SchemaSnapshot` | yes (`contracts.py:233-241`) | **sufficient shape** | keep as-is; extension happens *inside* the descriptors |
| `SchemaContext` | yes (`contracts.py:269-278`) | **sufficient** | no change |
| `TableRef`, `ColumnRef`, `JoinHint` | yes (`contracts.py:244-267`) | **sufficient** | no change |
| `TableDescriptor` | yes (`contracts.py:194-201`) | **needs extension** | add `column_details` + `description` (additive, §2) |
| `Relationship` | yes (`contracts.py:204-212`) | **needs extension** | add `kind` + `cardinality` (additive, §3) |
| `BusinessSemantics` | yes (`contracts.py:215-221`) | **sufficient** | no change |
| `SchemaVersion` | yes (`contracts.py:224-230`) | **sufficient** | no type change; `id` gains a semantic constraint (content fingerprint, §5) |
| `SchemaCatalog` Protocol | yes (`contracts.py:761-769`) | **needs one additive method** | add `refresh()` (§6) — aligns the Protocol with component-contracts §2, which already documents refresh |
| `SchemaRetriever` Protocol | yes (`contracts.py:772-780`) | **sufficient** | no change |
| `SchemaError` | yes (`contracts.py:681-684`, ADR-015 base) | **sufficient** | codes `SCH.UNAVAILABLE` / `SCH.DRIFT` / `SCH.VERSION_NOT_FOUND` / `SCH.NO_RELEVANT_SCHEMA` already registered in `interface-design.md` §7.4 |
| `ColumnDescriptor` | **absent** | **needed** | define in `contracts.py` (§2) — single canonical definition, re-exported nowhere else |

No renaming, no separation, no second definition of anything.

---

## 2. `ColumnDescriptor` (new value object) and `TableDescriptor` extension

```python
@dataclass(frozen=True)
class ColumnDescriptor:
    """Structural + semantic metadata for a single column."""

    name: str
    data_type: str
    nullable: bool
    key_role: str = "none"          # "none" | "pk" | "fk"
    description: Optional[str] = None


@dataclass(frozen=True)
class TableDescriptor:
    """Metadata for a single table. (existing — additive fields)"""

    name: str                        # fully qualified, e.g. "Sale.Orders"
    columns: tuple[str, ...] = ()
    primary_key: Optional[str] = None
    foreign_keys: tuple["Relationship", ...] = ()
    column_details: tuple[ColumnDescriptor, ...] = ()   # NEW
    description: Optional[str] = None                    # NEW
```

Field obligations (every field has a demonstrated reason — architecture doc §6):
* `data_type` / `nullable` — already introspected today, consumed by nothing; enable type-aware generation and drift detection.
* `key_role` — join planning and the documented presentation behavior of dropping `*Id` columns.
* `description` — the retrieval/grounding text (user-authored).
* Catalog invariant: `tuple(c.name for c in column_details) == columns`.

Explicitly **not** added (evaluated and rejected, architecture doc §6/§13): `default`, `aliases`, `sensitivity`, normalized/search fields. `sensitivity` is the documented future-compatible extension point (architecture doc §11).

## 3. `Relationship` extension

```python
@dataclass(frozen=True)
class Relationship:
    """Foreign-key or named relationship between two tables. (existing — additive fields)"""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    name: Optional[str] = None
    kind: str = "annotated"          # NEW: "database_constraint" | "annotated" | "inferred"
    cardinality: Optional[str] = None  # NEW: e.g. "1:N"; None when unknown
```

Reason: this database declares **no constraints** (documented, `database-profile.md` §2), so every current relationship is annotated; consumers (SemanticValidator, join planning) must be able to distinguish provenance instead of trusting all relationships equally. Only explicitly documented relationships enter a snapshot — none are inferred from column names.

## 4. `SchemaSnapshot` — unchanged shape, redefined key discipline

```text
version: SchemaVersion
tables: tuple[TableDescriptor, ...]
relationships: tuple[Relationship, ...]
business_semantics: tuple[BusinessSemantics, ...]
labels: Mapping[str, str]            # CANONICAL KEYS: "schema.table.column"
```

* `labels` keys become **table-qualified** (e.g. `"Sale.OrderItems.quantity"`). The current bare-name `COLUMN_MAP` cannot disambiguate `Id`, `quantity`, `status`, `entityDate`, `serialNo`, `unit`, `description`, `packCount`, `palletCount` — all of which occur in 2–3 tables.
* Consumers may fall back to a bare column name **only** when it is unambiguous across the snapshot (documented accessor behavior; exact helper decided in 3.1B).
* The existing test pin (`labels={"Id": "شناسه"}`) remains a *valid construction* (Mapping is opaque); the qualified-key discipline is a catalog production obligation, not a type change.

## 5. `SchemaVersion` — semantic constraint (no type change)

* `id` **must** be the sha256 content fingerprint of the snapshot's canonical serialization (architecture doc §8/§12).
* `captured_at` = generation timestamp; `source` = provenance string (e.g. `"introspection+annotations"`). Neither participates in the fingerprint.

## 6. `SchemaCatalog` Protocol — one additive method

```python
@runtime_checkable
class SchemaCatalog(Protocol):
    """Live, versioned schema metadata owner."""

    def current_version(self) -> str: ...
    def versions(self) -> tuple[SchemaVersion, ...]: ...
    def get_snapshot(self, version: Optional[str] = None) -> SchemaSnapshot: ...
    def refresh(self) -> SchemaSnapshot: ...   # NEW (additive)
```

`refresh()` is already part of the authoritative component contract (`component-contracts.md` §2 Input table) but was missing from the Protocol; this closes that gap additively. Implementations may also expose refresh at composition-root startup; the Protocol surface stays minimal.

## 7. Error contract (unchanged registry, ADR-015)

| Code | Producer | Meaning |
|---|---|---|
| `SCH.UNAVAILABLE` | SchemaCatalog | no snapshot available (no cache and introspection failed) |
| `SCH.DRIFT` | SchemaCatalog | content changed under a pinned version request |
| `SCH.VERSION_NOT_FOUND` | SchemaCatalog | requested version not in history |
| `SCH.NO_RELEVANT_SCHEMA` | SchemaRetriever | spec maps to no table (context with warnings; raise only on orchestrator policy) |
| `SCH.AMBIGUOUS` | SchemaRetriever | multiple equally likely candidates |

All codes are already registered in `interface-design.md` §7.4; nothing new enters the registry in 3.1B.

## 8. AgentState interaction (3.1A.23 — unchanged)

* `AgentState.schema_context: Optional[SchemaContext]` remains the **only** schema-related state field.
* Flow: `SchemaCatalog.get_snapshot()` → Orchestrator → `SchemaRetriever.retrieve(spec, snapshot)` → `SchemaContext` → `state.with_schema_context(...)` (existing applier, existing `SCHEMA_RESOLVED` transition — both already implemented and tested).
* The **persistent catalog and the snapshot are never placed in AgentState** (explicit task constraint; also keeps state request-sized).
* No new applier, no new state field, no Orchestrator change required for 3.1B wiring (the `OrchestratorDeps.schema_catalog`/`schema_retriever` seams already exist, `orchestrator.py:146-147,317-328`).

## 9. Boundary obligations for the implementation (enforcement design)

Executable rules to be added in 3.1B (architecture doc §16):

| Rule | Enforcement |
|---|---|
| `src/core/schema/**` imports no `pymssql` | AST layer test (whole-tree scan pattern from `test_boundary_hardening.py`) |
| `src/core/schema/**` imports no `src.core.llm.*`, no provider SDK, no `src.agents.state`, no `src.api/*`, no `src.memory` | same scan |
| only `src/core/database/{connection,metadata}.py` import `pymssql` | whole-tree SDK-site scan (extend the openai-pattern test to the driver) |
| `contracts.py` remains the only definer of `ColumnDescriptor` | canonical-ownership scan (3C pattern) |
| importing `src.core.schema.catalog` in a fresh subprocess loads no driver, opens no connection, writes no file | import-footprint + audit-hook probes (Sprint 3 pattern) |
| implementation satisfies the Protocol by **signature parity**, not merely `runtime_checkable` | conformance test (Sprint 3 pattern) |

## 10. Compatibility statement

* All contract changes are additive frozen-dataclass fields with defaults ⇒ existing constructions, tests, and serialization behavior are preserved.
* Identity tests (same object via any import path) trivially hold — no re-exports are added; `contracts.py` stays the sole definition site.
* No public API surface (`AskRequest`/`AskResponse`/`execute_query`/CLI/evaluation) is touched by the schema contracts.

## 11. Implemented inputs (exact, Sprint 3.1B)

* **Annotation file**: `data/schema/annotations.json` (UTF-8, `ensure_ascii=False`, pretty-printed, human-reviewable). Schema:

```json
{
  "annotation_version": 1,
  "source": "<provenance string describing where the annotations were seeded from>",
  "tables": {
    "<schema>.<table>": {
      "description": "<table description>",
      "columns": {"<column>": {"description": "<column description>"}}
    }
  },
  "relationships": [
    {"from_table": "...", "from_column": "...", "to_table": "...",
     "to_column": "...", "cardinality": "1:N"}
  ],
  "business_semantics": [
    {"description": "...", "formula": "quantity * unitPrice", "applies_to": ["<schema>.<table>.<column>", ...]}
  ],
  "labels": {"<schema>.<table>.<column>": "<Persian display label>"}
}
```

  Every entry is user-authored annotation; structural fields (existence, types, nullability, keys, ordinal) are never taken from this file. Annotations referencing unknown tables/columns are dropped and recorded as structured warnings (executable test: `tests/test_schema/test_catalog.py::TestAnnotationDrift`). The file hash participates in the refresh probe, so annotation edits create new versions.

* **Cache file**: `data/schema/schema_cache.json` — `{"cache_version": 1, "probe": {...}, "current": "<version id>", "snapshots": [{"version": {...}, "content": {...}}]}`; canonical JSON (`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`); newest-last history, retention bound = `SCHEMA_HISTORY_SIZE` (5, centralized constant in `src/core/schema/catalog.py`), oldest-first pruning. The legacy `.schema_cache.json` is untouched.

* **Retention / paths / refresh behavior**: see architecture doc §18 "Implementation Status" — documentation and implementation were updated together in 3.1B.

## 12. SchemaRetriever semantics (implemented 3.1C)

* `SchemaRetriever.retrieve(spec, snapshot) -> SchemaContext` — implemented in `src/core/schema/retriever.py` against the **unchanged** contract VOs (`QuestionSpec`, `SchemaSnapshot`, `SchemaContext`, `TableRef`, `ColumnRef`, `JoinHint`, `BusinessSemantics`). No contract shape was extended for retrieval; no duplicate types exist.
* Scoring signals, weights, limits, ambiguity and no-relevance semantics: `docs/architecture/schema-intelligence.md` §19 (centralized in `RetrievalPolicy`; deterministic; contract-shape-preserving — `SCH.*` conditions surface as `SchemaContext.warnings` per component-contracts §3, raised only as typed errors for invalid inputs).
* JoinHints are metadata (`ColumnRef` pairs); no SQL text is produced anywhere in the retrieval path.
* Wiring: the façade passes `SchemaCatalog` + `SchemaRetriever` through the existing `OrchestratorDeps` seams; `AgentState.schema_context` is written only by the Orchestrator via `with_schema_context`. **Dormant on the live path until a QuestionAnalyzer populates `question_analysis`** (proven by tests). One additive state transition (`SCHEMA_RESOLVED → SQL_GENERATED`, the established direct-path pattern) was required for the seam to complete without a QueryPlanner.

## 13. Phase 3.2 — production retrieval boundary (implemented)

Hardening of the 3.1C retriever, contract-shape-preserving (no VO changed; `SCH.CONTEXT_BOUNDED` added to the code registry as a context **warning**, same mechanism as `SCH.AMBIGUOUS`/`SCH.NO_RELEVANT_SCHEMA`):

* **Query-type-aware evidence routing**: filter fields are column-scoped (never table-name/description evidence); untyped entities and metric names remain dual-surface; `time_constraints`/`references` contribute nothing (documented prerequisite). Architecture doc §20.2.
* **Hard bounds** in `RetrievalPolicy`: `max_join_hints`, `max_semantic_entries`, `max_context_chars` (canonical serialized size via the public `serialized_context_size()`); deterministic trimming priority documented in architecture doc §20.4 — tables, join hints, endpoint columns and warnings are never removed; trimming is reported with the `SCH.CONTEXT_BOUNDED` warning (breach of the structural floor is reported, never hidden).
* **Telemetry**: one `schema_retrieval_completed` event per retrieval (counts/codes/sizes only). `RuntimeMetadata.schema_metrics` (additive dict) records counts + context size via the Orchestrator seam only; empty while dormant.
* **Rendering boundary**: `src/core/schema/renderer.py::SchemaContextRenderer` — `SchemaContext → LLM schema payload`; formatting only (context-only input, no DB/catalog/analyzer/router imports, no invention, direction preserved, deterministic, hard `max_chars` with documented section-drop priority and typed fail-closed error). Dormant until the authorized prompt-migration stage.
* **Leakage prevention**: executable tests pin that only `src/core/prompts.py` imports `src.core.database.models` (the legacy `FULL_SCHEMA` path), that retrieval/rendering/orchestrator modules never reference it, and that the SQLGenerator seam receives the retrieved context — never the snapshot or legacy text. Dataset-level protected-path leakage rate: 0%.
* **Evaluation**: `tests/test_schema/fixtures/retrieval_cases.json` (15 exact-expectation cases) + dataset metrics (precision/recall 1.0, bounded compliance 100%, leakage 0%).
