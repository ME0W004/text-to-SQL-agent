# Schema Intelligence — Current State Audit

**Sprint**: 3.1 — Prompt 3.1A (audit/design only; no production code changed)
**Status**: Audit record; basis for [`docs/architecture/schema-intelligence.md`](../architecture/schema-intelligence.md), [`docs/contracts/schema-intelligence.md`](../contracts/schema-intelligence.md) and [`ADR-017`](../adr/ADR-017-canonical-schema-representation.md)
**Evidence standard**: every claim below cites source inspection (`file:line`), a measured value, or a documented historical inspection. No live database connection was opened for this audit and no runtime observation is fabricated; live-DB facts cite the Sprint 0 inspection record (`docs/current-system/database-profile.md`) and are marked **[S0, documented]**.

---

## 1. Method

* Read all authoritative documents (AGENTS.md, ADR-002, ADR-011, ADR-016, package-architecture, dependency-rules, component-contracts, interface-design, agent-state contracts, llm-architecture, migration plan, Sprint 2/3 reports).
* Inspected every schema-related source file and its consumers (repo-wide greps for `FULL_SCHEMA`, `schema`, `table`, `column`, `relationship`, `foreign key`, `primary key`, `datatype`, `description`, `semantic`, `column map`, `schema hash`, `SchemaSnapshot`, `SchemaContext`, `SchemaCatalog`, `SchemaRetriever`).
* Measured the prompt schema footprint by importing the real modules (static, no LLM, no DB).
* Traced every consumer and non-consumer of each source.

---

## 2. Schema Sources Inventory (3.1A.1)

| # | Source | Location | Authority | Data provided | Freshness | How generated / updated | Who consumes it | Reliability | Known problems | Classification |
|---|--------|----------|-----------|---------------|-----------|--------------------------|-----------------|-------------|----------------|----------------|
| S1 | Live DB metadata (columns only) | `INFORMATION_SCHEMA.COLUMNS` read by `src/core/schema_hash.py:15-25` and `scripts/generate_column_map.py:12-37` | Ground truth (structural, partial) | table name, column name, data type, nullability (hash); table+column names (generator) | Refreshed only at façade construction | Direct SQL introspection over the shared `DatabaseConnection` | column-map pipeline only | Reliable for what it selects, but scope-limited | Reads **only** `Sale` + 3 tables; no PK/FK/constraint/default/index discovery anywhere in the repo | Canonical (partial — columns only) |
| S2 | Static table models | `src/core/database/models.py:6-50` (`ORDERS_MODEL`, `ORDER_ITEMS_MODEL`, `SHIPMENTS_MODEL`, `ALL_MODELS`) | None (orphaned) | table name, column list, declared PK, declared logical FKs | Frozen at authoring; never refreshed | Hand-authored | **Zero consumers** (grep-verified: only self-references and `__all__`) | Accurate against DB column lists **[S0]** but claims FKs the DB does not declare **[S0: `sys.foreign_keys` empty]** | Dead export set; `ORDERS_MODEL.foreign_keys=[]` inconsistent with the other two | Legacy (dead) |
| S3 | `FULL_SCHEMA` prompt text | `src/core/database/models.py:81-125` | De-facto prompt authority | Semantic column descriptions (Orders, OrderItems), relationship list, business rules | Hand-authored once; never refreshed | Hand-authored | `src/core/prompts.py:3,19` (embedded verbatim in every SQL-generation prompt) | **Proven drift**: no `Sale.Shipments` table section at all; claims `buyerId` → "Buyers table" which does not exist **[S0]**; `mesc` described as material code (correct per **[S0]**) while S4 translates it wrong | Prompt-only truth; not machine-readable | Legacy / Prompt-only |
| S4 | `COLUMN_MAP` Persian labels | `src/core/column_mapper.py` (auto-generated) | None (presentation, unused) | column name → Persian display label (41 entries) | Regenerated only when structural hash changes | **LLM-generated** translations (`scripts/generate_column_map.py:40-97`), chunked, identity fallback on failure | Imported by `src/agents/text_to_sql_agent.py:50` (try/except) and `src/core/__init__.py:3` — **never consumed** (dead import) | Low: output varies per LLM run **[S0]**; contains stale key `delivered_quantity` present in no table; `mesc` → «مقدار» contradicts S3 ("material code"); keyed by **bare column name** → cross-table collisions (`Id`, `entityDate`, `quantity`, `status`, … appear in 2–3 tables) | Loaded at startup, used nowhere | Legacy / Presentation-only (dead) |
| S5 | Column-map maintenance pipeline | `src/core/column_mapper_updater.py` + `scripts/generate_column_map.py` + `scripts/src/core/column_mapper.py` (stray copy) | Maintenance utility | Regenerates S4 when S6 hash differs | On façade construction | hash compare → `subprocess.run` of the generator (30 s timeout) | `TextToSQLAgent.__init__` (`text_to_sql_agent.py:70`) | Works, but spawns a subprocess that calls the **LLM** at agent startup when the schema changed | LLM-in-the-loop at startup; regenerated labels are non-deterministic; stray `scripts/src/` tree from a wrong-cwd run | Legacy (maintenance) |
| S6 | Schema hash + cache | `src/core/schema_hash.py` (MD5 of `TABLE.COLUMN.TYPE.NULLABLE` rows) + `.schema_cache.json` (`{"schema_hash": "2227c4b73127620821e927d1a6b56692"}`) | Cache invalidation key only | Single opaque MD5 | Updated after each regeneration | Computed at construction, compared to cache | `column_mapper_updater.ensure_column_map_is_updated` | Reliable for its narrow purpose | MD5 (weak, though non-security use); covers only columns of 3 tables; serves as **cache key, not schema identity** | Cache |
| S7 | Prompt rules (query-generation metadata) | `src/core/prompts.py:21-41` (16 STRICT rules) | De-facto authority for SQL conventions | join paths, `TOP`/`HAVING`/`LEFT JOIN` rules, revenue formula, Jalali conventions, count-vs-sum distinction | Hand-authored | Hand-authored | every SQL-generation prompt | Consistent with few-shots; embeds relationships and semantics that belong to structured metadata | Not derived from any catalog; duplicates S3 content | Prompt-only |
| S8 | Few-shot examples | `src/core/sql_extractor/few_shots.py` (5 examples) | Convention demonstrator | join patterns, revenue convention, `COUNT(DISTINCT)` idiom | Hand-authored | Hand-authored | `prompts.py:50-51` | One example filters `jalaliYearNo='1403'` — outside the data range 1394–1399 **[S0]** → teaches an empty-result pattern | Encodes semantics imperatively | Prompt-only |
| S9 | Compact schema builder | `models.py:55-76` `build_compact_schema()` | None | 882-char compact schema string | Frozen | Hand-authored | **Zero consumers** | n/a | Dead code | Legacy (dead) |
| S10 | Evaluation grounding facts | `evaluation/baseline_questions.json` (`meta.grounding`) | Test-only truth | verified schema facts (tables, value domains) | Static | Authored from Sprint 0 inspection | evaluation harness | Accurate **[S0]** | Test-only | Test-only |
| S11 | Sprint 0 live inspection record | `docs/current-system/database-profile.md`, `docs/current-system/database-semantics-audit.md` | Documentation of ground truth | full column/type/nullability/value inventories, constraint absence, semantic hazards | As of Sprint 0 (2026-08) | Read-only live inspection | documentation / this audit | Documented, not machine-consumable | Not wired into the system | Documentation |
| S12 | Empty `src/schemas/` package | `src/schemas/__init__.py` (empty) | None | — | — | — | none | n/a | Vestigial | Legacy (dead) |

**Answer to "what is canonical today?"** — nothing is. Structural truth lives in the DB but is only partially introspected (S1, columns of 3 tables, for a cache key); semantic truth lives in hand-authored prompt text (S3/S7/S8); presentation truth is dead, LLM-derived, and stale (S4). ADR-002's premise ("static `FULL_SCHEMA` drift; Shipments omission") is confirmed by direct inspection.

---

## 3. Live Database Metadata Capabilities (3.1A.2)

Repository code can discover from SQL Server **today**:

| Capability | Present? | Evidence |
|---|---|---|
| Table discovery (scoped) | Partial | `INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='Sale' AND TABLE_NAME IN (...)` — the table list itself is **hardcoded** (`schema_hash.py:22-23`, `generate_column_map.py:28-30`) |
| Column discovery | Yes (names/types/nullability) | same queries |
| Primary keys | **No** | no query anywhere touches `sys.key_constraints` / `TABLE_CONSTRAINTS` (grep-verified) |
| Foreign keys | **No** | no `sys.foreign_keys` / `REFERENTIAL_CONSTRAINTS` query anywhere |
| Indexes / constraints / defaults / comments / row statistics | **No** | absent (grep-verified) |

Hardcoded today: the table universe (3 tables), the schema name (`Sale`), the relationships (prompt text + model dicts), all semantics. Deployment-specific knowledge required: none for columns (introspectable); relationships are **not** discoverable from this database because it declares no constraints **[S0]** — they must come from annotation until constraints exist.

No live connection was opened for this audit (design-only stage). DB env keys are present (`.env`), but runtime observations would violate the stage's no-runtime-call rule; **[S0]** records are used instead.

---

## 4. Current Database Inventory (3.1A.3)

Evidence: `models.py` column lists (cross-checked against **[S0]** Sprint 0 inspection — they match), `FULL_SCHEMA` descriptions, **[S0]** `database-profile.md`.

Schema: **`Sale`** (single user schema). Database: `AgentiveAi` **[S0]**.

### Sale.Orders — purpose: order headers (contract-level sales records)
| Column | Type | Nullable | Key role | Default | Semantic description | Notes |
|---|---|---|---|---|---|---|
| Id | int | NO | PK | UNKNOWN | unique order identifier | **[S0]** no PK constraint declared in DB |
| supplierUser | nvarchar(100) | NO | — | UNKNOWN | sales representative who created the order | |
| entityDate | date | YES | — | UNKNOWN | order creation date (Gregorian) | **[S0]** range 2016-01-11 → 2021-02-25 |
| serialNo | bigint | YES | — | UNKNOWN | sequential order number | distinct from Id **[S0]** |
| status | nvarchar(300) | NO | — | UNKNOWN | order lifecycle ('اتمام سفارش' completed, 'تایید باربری' shipping confirmed, 'ابطال' cancelled) | value meanings from glosses; **[S0]** value distribution |
| jalaliYearNo | varchar(4) | YES | — | UNKNOWN | Persian calendar year ('1399') | canonical Jalali filter column |
| operationMonth | int | YES | — | UNKNOWN | Jalali YYYYMM (e.g. 139901) | canonical month grouping column |
| buyer | nvarchar(200) | NO | — | UNKNOWN | customer / company name | free text |
| buyerId | int | YES | (logical FK, unresolved) | UNKNOWN | customer ID | **dangling — no Buyers table exists [S0]** |
| currencyRate | decimal(18,2) | NO | — | UNKNOWN | exchange rate for the order | **[S0]** nothing uses it |
| curency | nvarchar(10) | YES | — | UNKNOWN | currency code (RLS/USD/RUB/EUR/AED) | misspelled column name is a fact |
| destination | nvarchar(500) | NO | — | UNKNOWN | destination (mixed provinces/countries) | **[S0]** mixed granularity |
| destinationId | int | NO | (dangling) | UNKNOWN | destination ID | no lookup table **[S0]** |
| orderTypeId | int | YES | (dangling) | UNKNOWN | order type ID | no lookup table **[S0]** |
| orderType | nvarchar(100) | YES | — | UNKNOWN | order type description («فروش کارگزاری بورس» …) | |
| bourseContractNo | nvarchar(100) | YES | — | UNKNOWN | stock-exchange contract number | |

### Sale.OrderItems — purpose: order line items (products, quantities, prices)
| Column | Type | Nullable | Key role | Default | Semantic description | Notes |
|---|---|---|---|---|---|---|
| Id | int | NO | PK | UNKNOWN | unique item identifier | **[S0]** |
| orderId | int | NO | FK (logical) → Sale.Orders.Id | UNKNOWN | owning order | relationship is logical only — no DB constraint **[S0]** |
| itemNo | int | NO | — | UNKNOWN | line number within order | |
| orderNo | nvarchar(50) | YES | — | UNKNOWN | business document number ('AP02833/03') | **does NOT match Orders.Id [S0]** — hazard |
| mesc | nvarchar(12) | NO | — | UNKNOWN | material code (zero-padded) | `COLUMN_MAP` mistranslates as «مقدار» |
| unit | nvarchar(2) | YES | — | UNKNOWN | unit of measurement (KG/TN/LT/M3) | **[S0]** mixed units hazard |
| description | nvarchar(max) | YES | — | UNKNOWN | product description | |
| miscInfo | nvarchar(50) | YES | — | UNKNOWN | additional info ('ON'/'OFF1'/'OFF2') | |
| productGroup | nvarchar(10) | NO | — | UNKNOWN | product category (HDPE/GPPS/HIPS/EPS/ABS/LIQUID) | |
| partNo | nvarchar(50) | YES | — | UNKNOWN | product grade / part number ('3840 UA') | |
| quantity | float | YES | — | UNKNOWN | ordered quantity | unit given by `unit`; **[S0]** 0 … 3,045,649 |
| unitPrice | decimal(18,5) | YES | — | UNKNOWN | price per unit | denominated in `curency` (assumption, strongest reading **[S0]**) |
| packCount | int | YES | — | UNKNOWN | number of packages | |
| palletCount | int | YES | — | UNKNOWN | number of pallets | |
| discount | decimal(18,2) | NO | — | 0.00 (documented in FULL_SCHEMA; **[S0]** always 0.00) | discount amount | |
| vatPercent | decimal(5,2) | NO | — | UNKNOWN | VAT percentage (0/9/10) | **[S0]** avg ≈ 8.96 |

### Sale.Shipments — purpose: physical shipments/deliveries per order item
| Column | Type | Nullable | Key role | Default | Semantic description | Notes |
|---|---|---|---|---|---|---|
| Id | int | NO | (nominal PK) | UNKNOWN | shipment identifier | **NOT unique in practice — 795 duplicate Id groups [S0]** |
| orderItemId | int | YES | FK (logical) → Sale.OrderItems.Id | UNKNOWN | owning order item | logical only **[S0]** |
| serialNo | bigint | YES | — | UNKNOWN | own sequence | collides semantically with Orders.serialNo naming |
| fullEntityNo | nvarchar(200) | YES | — | UNKNOWN | shipment document number | |
| entityDate | date | YES | — | UNKNOWN | shipment date | |
| quantity | float | YES | — | UNKNOWN | delivered quantity | **[S0]** 15 items over-delivered (Σ > ordered) |
| packCount / palletCount | int | YES | — | UNKNOWN | packaging counts | |
| packingWeight / netWeight / grossWeight | decimal(18,3) | YES | — | UNKNOWN | weights | |
| vehicleIdentificationNo | nvarchar(100) | YES | — | UNKNOWN | truck plate / vehicle id | |
| driverId | int | YES | (dangling) | UNKNOWN | driver ID | no lookup table **[S0]** |
| driver | nvarchar(200) | YES | — | UNKNOWN | driver name | |
| typeId | int | NO | (dangling) | UNKNOWN | shipment type ID | no lookup table **[S0]** |
| typeName | nvarchar(500) | YES | — | UNKNOWN | shipment type («بارگیری» loading, «برگشت از فروش» sales return, «عودت» return) | **[S0]** distribution |

`UNKNOWN` is used literally wherever the repository provides no evidence (defaults are mostly unknown; per-column defaults are not introspected today).

---

## 5. Relationship Audit (3.1A.6)

| Source table | Source column | Target table | Target column | Type | Confidence / provenance |
|---|---|---|---|---|---|
| Sale.OrderItems | orderId | Sale.Orders | Id | 1:N (many items per order) | Explicit in code: `models.py:30-32` FK dict; `models.py:121`; `models.py:66` compact; `prompts.py:34`; few-shots. **No DB constraint** **[S0]** |
| Sale.Shipments | orderItemId | Sale.OrderItems | Id | 1:N | Explicit in code: `models.py:47-49`; `models.py:75`; `models.py:71` compact; `prompts.py:35`. **No DB constraint** **[S0]** |

That is the complete explicitly-known set. `FULL_SCHEMA` additionally *claims* `buyerId → "Buyers table"` (models.py:92) — **false** **[S0]** (no Buyers table exists); it is excluded from the relationship set above. All other same-name columns (e.g. `Orders.buyerId` → anything, `driverId`, `typeId`, `destinationId`) are dangling references with no resolvable target — deliberately **not** inferred from column names.

Current code does **not** rely on any machine-readable relationship store: the LLM infers joins from `FULL_SCHEMA` + prompt rule 12 + few-shot patterns.

---

## 6. Semantic Information Audit (3.1A.4, 3.1A.5)

Extracted business concepts (evidence: FULL_SCHEMA glosses, prompt rules, few-shots, **[S0]** semantics audit):

| Concept | Where encoded today | Classification | Future owner (design, not merged yet) |
|---|---|---|---|
| revenue = Σ `quantity * unitPrice` | FULL_SCHEMA "Important" note; prompt rule 10; 5/5 few-shots | Business semantic (formula) | `SchemaCatalog` → `BusinessSemantics` |
| Jalali year filtering via `jalaliYearNo`; month grouping via `operationMonth` | prompt rules 4–5; few-shots | Business semantic (time semantics) | `SchemaCatalog` (column descriptions) + future planner guidance |
| «buyer» = customer | FULL_SCHEMA buyer gloss | Business semantic (term) | future QuestionAnalyzer vocabulary (NOT catalog — see boundary doc) |
| «productGroup» = polymer category | FULL_SCHEMA gloss; **[S0]** value domain | Business semantic (dimension meaning) | `SchemaCatalog` (column description) |
| «quantity» = number of units (KG-dominant) | FULL_SCHEMA gloss; **[S0]** unit hazard | Business semantic (measure + unit) | `SchemaCatalog` (column description) |
| status value meanings («اتمام سفارش» etc.) | FULL_SCHEMA status gloss; **[S0]** distribution | Business semantic (enum meaning) | `SchemaCatalog` (column description) |
| mesc = material code | FULL_SCHEMA gloss (+ **[S0]** samples) — contradicted by S4 label | Business semantic | `SchemaCatalog` (column description); fix label at migration |
| delivery semantics (Shipments.quantity vs ordered) | **[S0]** §2/§5 (memory-DB questions) — absent from prompts | Business semantic gap | `SchemaCatalog` (table/column description) |
| join paths | models.py FK dicts, FULL_SCHEMA, prompt rule 12, few-shots | Structural metadata (duplicated ×4) | `SchemaCatalog` → `Relationship` |
| Persian display labels | `COLUMN_MAP` | Presentation metadata | `SchemaSnapshot.labels` (migrate) |
| SQL conventions (TOP/HAVING/LEFT JOIN/aliases/count-vs-sum) | prompt rules 1–3, 7–9, 13–16 | Query-generation metadata | future QueryPlanner/SQLGenerator policy (NOT catalog) |
| Table universe = 3 Sale tables | prompt rule 11; hash scope; generator scope | Structural scope statement | `SchemaCatalog` discovery scope config |

Key finding: **semantic knowledge and presentation labels are already different things in the code** (FULL_SCHEMA prose vs COLUMN_MAP), but they share the same failure mode — hand/LLM-authored text with no versioning, no provenance and no consistency check (the `mesc` contradiction is live evidence).

---

## 7. Current Schema Versioning (3.1A.7)

Actual behavior (`schema_hash.py`, `column_mapper_updater.py`, `.schema_cache.json`):

1. **What is hashed**: for the 3 hardcoded tables, each row of `INFORMATION_SCHEMA.COLUMNS` as `TABLE_NAME.COLUMN_NAME.DATA_TYPE.IS_NULLABLE |`, concatenated in `ORDINAL_POSITION` order, MD5-hashed.
2. **When**: every `TextToSQLAgent()` construction (`text_to_sql_agent.py:69-70`) — i.e., every process startup (agent is per-process).
3. **What changes trigger an update**: any column add/remove/rename/type/nullability change within the 3 tables.
4. **What happens after a change**: `ensure_column_map_is_updated` runs `scripts/generate_column_map.py` in a subprocess → the script connects to the DB, re-reads columns, calls the **LLM** (chunked) to translate names, rewrites `column_mapper.py`.
5. **What is cached**: a single MD5 in `.schema_cache.json`. **What is invalidated**: nothing else — `FULL_SCHEMA`, prompt rules, few-shots, and the model dicts are never updated. **What is regenerated**: only `COLUMN_MAP`.
6. **Failure behavior**: any exception in the hash check/regeneration is caught and logged (`return False`); agent construction continues (map stays stale). Cache file corrupt → treated as no-cache.

Conclusion: the mechanism is a **column-map cache invalidator**, not schema versioning. There is no schema identity, no history, no consumer of the hash beyond regeneration.

---

## 8. Schema Drift Matrix (3.1A.8)

| Change | Detected today? | Mechanism | Consequence when it happens | Reliable? |
|---|---|---|---|---|
| New column (3 tables) | Yes | hash mismatch → map regen | `COLUMN_MAP` gains an LLM translation; **prompt schema NOT updated** → LLM unaware | Partial (structurally detected; semantically stale) |
| Removed column | Yes | hash mismatch | map regen; prompt stale; queries referencing it fail at execution | Partial |
| Renamed column | Yes | hash sees remove+add | map regen (LLM output may vary run-to-run); prompt stale | Partial |
| Data type change | Yes | `DATA_TYPE` in hash | triggers map regen — but the regenerated artifact contains **no type information**, so the regeneration is a no-op for consumers | Detected, no useful effect |
| Nullability change | Yes | `IS_NULLABLE` in hash | same as datatype — no consumer | Detected, no useful effect |
| **New table (Sale, outside the 3)** | **No** | hash + generator + prompt rule 11 all scoped to the 3 tables | invisible to the whole system | No |
| Removed table | No | same scoping | queries fail at execution | No |
| Relationship change | No | nothing hashes/reads relationships (DB declares none) | joins silently wrong | No |
| Semantic description drift | No | hand-authored text, no mechanism | wrong grounding persists | No |
| Label semantic change | No | labels only regenerate on structural hash change | stale/wrong labels persist | No |

---

## 9. Current Prompt Schema Analysis (3.1A.9)

Measured by importing the real modules (static; no LLM call):

| Quantity | Value |
|---|---|
| `FULL_SCHEMA` size | 1,946 chars / 266 words / 44 lines |
| Tables described in `FULL_SCHEMA` | **2** (Orders, OrderItems) — Shipments appears only in the relationship section |
| Columns described | 32 of 48 (Shipments' 16 columns absent) |
| Total `sql_generation_prompt` (no history) | 6,048 chars (schema ≈ 32%; rules ≈ 2,300 chars; 5 few-shots ≈ 1,800 chars) |
| Total with 2-message history | 6,286 chars (history capped at last 5 messages, `prompts.py:55`) |
| Few-shot examples | 5 (all Orders⋈OrderItems; none uses Shipments) |
| Error-correction prompt | 271 chars + error + invalid SQL (**no schema re-supplied** — correction is schema-blind except by memory) |
| Estimated token footprint | ~1,500–1,800 tokens per SQL-generation call (≈4 chars/token mixed English/Persian — estimate, not measured against a tokenizer) |
| Ordering / structure | static string; tables in fixed order; every request pays the full cost regardless of question scope |
| Redundancy | relationships appear 3× in the prompt (FULL_SCHEMA section, rule 12, few-shots); revenue formula appears 3× (FULL_SCHEMA note, rule 10, few-shots) |

This is the inefficiency Schema Retrieval targets: 100% of schema knowledge is sent on 100% of requests, and the payload itself is provably incomplete (Shipments) and partially wrong (Buyers claim).

---

## 10. Schema Duplication Findings (3.1A.10)

| Information | Copies (all verified) | Canonical today | Why duplicates exist | Migration strategy (design) |
|---|---|---|---|---|
| Table + column names | DB (S1), model dicts (S2), FULL_SCHEMA (S3), compact builder (S9), hash scope (S6), generator scope | DB (only partial) | incremental history; prompt needs text | `SchemaCatalog` discovers; all others become derived or die |
| Relationships | S2 dicts, S3 FULL_SCHEMA, S7 rule 12, S8 few-shots | none (all hand-authored) | no machine-readable store existed | `SchemaSnapshot.relationships` (annotated provenance) |
| Business semantics (revenue, Jalali) | S3, S7, S8 | none | prompt-only era | `SchemaSnapshot.business_semantics` + planner/generator policy |
| Persian labels | S4 (only live copy; Sprint-2 report references a removed `_filter_and_translate_rows()` consumer) | S4 (unused) | presentation layer not yet built | `SchemaSnapshot.labels` (table-qualified keys) |
| Column types/nullability | DB (S1), nowhere else | DB | nothing consumes types | `ColumnDescriptor` |
| Schema change signal | S6 hash (only) | S6 | cache invalidation only | catalog version identity (content fingerprint) |

Every duplicate will have exactly one canonical owner under the target design; derivations must be generated **from** the canonical snapshot, never hand-maintained alongside it.

---

## 11. Consumer/Non-consumer Summary (evidence for migration risk)

* `ORDERS_MODEL`/`ORDER_ITEMS_MODEL`/`SHIPMENTS_MODEL`/`ALL_MODELS`: zero consumers (grep over `src`, `tests`, `scripts`, `evaluation`).
* `build_compact_schema()`: zero consumers.
* `COLUMN_MAP`: imported (`text_to_sql_agent.py:50`, `src/core/__init__.py:3`), never read. The Sprint 2 consumer (`_filter_and_translate_rows`) no longer exists.
* `FULL_SCHEMA`: one consumer — `prompts.py`.
* `schema_hash`/`ensure_column_map_is_updated`: one consumer — façade construction.
* Contracts `SchemaSnapshot`/`SchemaContext`/`SchemaCatalog`/`SchemaRetriever`: defined and tested (`tests/test_agents/test_contracts.py`, `test_state_appliers.py`, `test_architecture/test_validation.py`); the Orchestrator has the full wiring path (`orchestrator.py:317-328`) but no production component supplies a real snapshot — `_run_schema_resolution` currently applies an empty `SchemaContext()` only when a QuestionSpec exists (which today it never does, so `schema_context` stays `None` on the live path).

---

## 12. Documentation Discrepancies Noted

* `docs/implementation/sprint-3a-report.md` (listed as authoritative in the 3.1A task) **does not exist**; the 3.0 stage record is `docs/sprints/sprint-3-prompt-3.0c-report.md` (this discrepancy was already recorded in the 3.0C report, §28 D2). Nothing was invented to fill the gap.
* `scripts/src/core/column_mapper.py` is a stray duplicate artifact of the generator script being run with the wrong working directory. Repo hygiene item; untouched in this stage.
* `interface-design.md` §Q2 ("single interface or split read/write SchemaCatalog") and Sprint-2 report Q10 ("refresh auto-at-startup or admin-only?") were explicitly deferred to the schema sprint — resolved by this design (see architecture doc §8–§9 and ADR-017).
