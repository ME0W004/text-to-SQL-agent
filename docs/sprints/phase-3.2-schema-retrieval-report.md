# Phase 3.2 Report — Schema Retrieval & Bounded Schema Context Activation

**Date**: 2026-09-13 · **Branch**: `new-ar` · **Stage**: Phase 3.2 (production retrieval boundary on top of Sprint 3.1)
**Final status**: **PASS WITH DEBT** (§38; the debt is the unchanged, explicitly documented QuestionAnalyzer prerequisite — Case B re-confirmed — plus pre-existing migration debt; the retrieval boundary itself is complete, bounded and evaluated)

---

## 1. Executive Summary

Phase 3.2 turned the Sprint 3.1C retrieval foundation into a **production retrieval boundary** without rebuilding any 3.1 component and without changing live runtime behavior. The preflight audit re-confirmed **Case B** (no production `QuestionAnalyzer` exists; `question_analysis` is always `None` on the live path), so question-driven retrieval remains dormant by design; everything provable without it was implemented, tested and evaluated: query-type-aware evidence routing (filter fields are column-scoped evidence — Phase 8), hard context bounds (`max_join_hints`, `max_semantic_entries`, `max_context_chars` — Phase 9), deterministic context budgeting with a documented drop priority and explicit `SCH.CONTEXT_BOUNDED` reporting (Phase 10), the contract-named `schema_retrieval_completed` telemetry event (Phase 15), additive `RuntimeMetadata.schema_metrics` written only by the Orchestrator (Phase 16), the dormant `SchemaContextRenderer` LLM-facing rendering boundary (Phases 12-13), executable full-schema leakage prevention pinning the legacy `FULL_SCHEMA` surface (Phase 11), a 15-case exact-expectation evaluation dataset with dataset metrics (Phases 18-19), determinism/concurrency/performance proof (Phases 17/22/28) and documentation matching implementation (Phase 29). Full regression: **1064 passed / 356 skipped / 0 failed** vs baseline 975/356/0 (+89, exactly reconciled §33); compileall clean. Protected-path **full-schema leakage rate = 0%** (executable evidence §22).

## 2. Baseline Before Implementation

Recorded fresh at Phase 0, before any change:

| Command | Result |
|---|---|
| `uv run python -m pytest tests -q` | **975 passed, 356 skipped, 0 failed** (108.69 s) |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean |

Exact match with the Sprint 3.1C end state. Skips = the by-design protocol-matrix parametrization (unchanged throughout — no protocol method was added or removed in Phase 3.2).

## 3. Current Runtime Retrieval Status

The live request path (`/api/v1/ask` → façade → Orchestrator) is **unchanged**: `question_analysis` is `None`, so `_run_schema_resolution` returns early, no catalog read occurs, no context is fabricated, `FULL_SCHEMA` still feeds the legacy SQL-generation prompt. The audit matrix:

| Stage | Status | Evidence |
|---|---|---|
| question → QuestionAnalyzer | **DORMANT** | No production implementation exists anywhere in `src/` (grep-verified; only the Protocol, the Optional dep and docstrings) |
| QuestionSpec | **DORMANT** | Nothing populates `state.question_analysis` on the live path |
| SchemaCatalog | **WIRED, DORMANT** | Façade-constructed, injected via `OrchestratorDeps`; 0 `get_snapshot()` reads on live requests (seam test) |
| SchemaSnapshot | **DORMANT** | Produced only on the injected-spec test path |
| SchemaRetriever | **WIRED, DORMANT** (hardened) | Constructed at the façade; now bounds/budgets/telemetrizes every retrieval |
| SchemaContext | **DORMANT** | Orchestrator-applier-only writes; no generation component consumes it |
| downstream consumer → LLM | **LEGACY (transitional dual-schema path)** | `SQLExtractor` → `sql_generation_prompt` embeds `FULL_SCHEMA` verbatim (`src/core/prompts.py:19`); `error_correction_prompt` is schema-blind; `natural_response_prompt` receives rows only |
| `FULL_SCHEMA` | **LEGACY — LIVE** | Sole LLM-facing schema source; import surface pinned (§22) |

## 4. QuestionSpec Availability

Re-evaluated per Phase 2: **Case B again.** Who produces a `QuestionSpec` today: nobody. `OrchestratorDeps.question_analyzer` is `None`; no `QuestionAnalyzer` implementation exists in `src/`. Therefore: no analyzer was fabricated; all retrieval-boundary enforcement that is provable independently was implemented against **injected** specs; the evaluation dataset is spec-driven; runtime activation remains blocked by the documented QuestionAnalyzer prerequisite (§37). No retrieval signal is wasted when the analyzer lands: the seam, applier, transition, bounds, budgeting, telemetry and metrics are all in place and tested end-to-end with fakes.

## 5. Architecture Path

```
question ──► QuestionAnalyzer        DORMANT (no implementation — Phase 24 prerequisite)
              ↓ QuestionSpec
            SchemaCatalog.get_snapshot()   WIRED/DORMANT (seam; 0 live reads)
              ↓ SchemaSnapshot
            SchemaRetriever.retrieve()     WIRED/DORMANT — HARDENED (bounds, budget, telemetry)
              ↓ SchemaContext (bounded; SCH.* warnings; deterministic)
            Orchestrator (sole writer) → AgentState.schema_context + RuntimeMetadata.schema_metrics
              ↓
            SchemaContextRenderer          NEW, DORMANT (LLM-facing payload boundary)
              ↓
            LLM                            LEGACY path: FULL_SCHEMA (unchanged, documented)
```

## 6. Existing 3.1C Components Reused (not rebuilt)

`SchemaCatalog`, `SchemaSnapshot`, `SchemaContext`, `QuestionSpec`, `TableRef`/`ColumnRef`/`JoinHint`, `BusinessSemantics`, the `SchemaRetriever` Protocol, `OrchestratorDeps` seams, the state applier and the `SCHEMA_RESOLVED → SQL_GENERATED` transition — all consumed as-is. **Zero contract VO changes** (no field added, renamed or moved; `contracts.py` untouched in this phase).

## 7. Changed Files

**Production (4)**

| File | Change |
|---|---|
| `src/core/schema/retriever.py` | Hardening: fixed the duplicated `_score_all_columns()` call and a stale docstring ("≥ 8" → ≥ `w_qualified_table` = 10) — production gaps found in audit; query-type-aware term routing (`_Terms` gains `filters`/`metrics` groups); policy bounds `max_join_hints=12`, `max_semantic_entries=8`, `max_context_chars=8000`; public `serialized_context_size()`; `_bound_context()` deterministic budgeting; `SCH.CONTEXT_BOUNDED` warning; `schema_retrieval_completed` telemetry via `log_event` |
| `src/core/schema/renderer.py` | **NEW** — `SchemaContextRenderer`: deterministic bounded rendering of a `SchemaContext` (formatting only; context-only input; hard `max_chars` with documented section-drop priority; typed fail-closed error at the structural floor) |
| `src/agents/state.py` | `RuntimeMetadata.schema_metrics: dict[str, int]` (additive field + `with_schema_metrics`), `AgentState.with_schema_metrics` applier, additive `to_dict` key |
| `src/agents/orchestrator.py` | `_run_schema_resolution`: records `schema_resolution` stage timing + `schema_metrics` (counts + context size) when retrieval runs; imports the canonical `serialized_context_size` |

**Tests (8)**

| File | Change |
|---|---|
| `tests/test_schema/test_retrieval_hardening.py` | **NEW** — 22 tests: routing, bounds, budgeting, size measure, telemetry |
| `tests/test_schema/test_context_renderer.py` | **NEW** — 14 tests: sections, determinism, input contract, no-invention, size-limit tiers |
| `tests/test_schema/test_retrieval_determinism.py` | **NEW** — 14 tests: `PYTHONHASHSEED` subprocess determinism, label-dict order, ties, duplicates, concurrency/isolation, policy immutability, performance/scaling |
| `tests/test_schema/test_retrieval_evaluation.py` | **NEW** — 21 tests: 15 dataset cases + 6 metric assertions |
| `tests/test_schema/fixtures/retrieval_cases.json` | **NEW** — the evaluation dataset |
| `tests/test_schema/test_orchestrator_seam.py` | +5 tests: metrics recording (active/dormant/counts-only), full-schema leakage on the SQLGenerator seam (2) |
| `tests/test_architecture/test_schema_boundary.py` | +1 footprint target (renderer); new `TestFullSchemaLeakagePrevention` (7 tests); auto per-file matrix instances for `renderer.py` (+2) |
| `tests/test_architecture/test_boundary_hardening.py` | `SIDE_EFFECT_TARGETS` gains `src.core.schema.renderer` (+3 probe instances) |

**Documentation (7)**: `docs/architecture/schema-intelligence.md` (§20 + status header), `docs/contracts/schema-intelligence.md` (§13 + status header), `docs/architecture/dependency-rules.md` (§3.3 note + §3.3a renderer), `docs/architecture/package-architecture.md` (tree), `docs/adr/ADR-017-canonical-schema-representation.md` (Phase 3.2 addendum), `docs/contracts/interface-design.md` (§7.4 `SCH.CONTEXT_BOUNDED`), `docs/contracts/component-contracts.md` (§3 errors/telemetry payload/output bounds), plus this report.

## 8. Table Selection

Unchanged 3.1C mechanics (score → threshold → sort (score desc, name) → `table_top_k`), now fed by type-routed terms. Verified consequences of routing (tests): a filter field whose text matches a table description no longer boosts that table (twin-table test: the previous silent description tie-break now correctly yields a tie → `SCH.AMBIGUOUS`); metric names still match table descriptions (concept evidence); untyped entities unchanged; exact matches outrank weak semantic matches (evaluation cases).

## 9. Column Selection

Unchanged mechanics (per-table score → `min_column_score` → sort (score desc, ordinal, name) → `column_top_k_per_table`; hand-built snapshots usable via name-only pass). Column inclusion remains evidence-driven; the new budget may drop non-endpoint columns lowest-score-first (§16 below) — endpoints never.

## 10. Relationship Retrieval

Unchanged: undirected bounded BFS over snapshot relationships only (`max_join_path_edges`), canonical direction on hints, sorted expansion, visited-set cycle prevention, one shortest path per pair, duplicate-edge elimination, deterministic hint ordering. **No inferred relationships** (dangling-`buyerId` evaluation case; `kind` never fabricated). New: hints capped at `max_join_hints` (canonically-first kept).

## 11. Semantic Retrieval

Approved deterministic surfaces only: table/column descriptions, Persian labels (exact equality), business-semantics description/formula tokens. Tokenization: Unicode `\w+` word tokens, casefolded (Persian-safe); **no stopwords** (curated metadata text; documented); no external NLP dependency; no embeddings (explicit non-goal). Persian evaluation cases: «راننده» → `Sale.Shipments.driver` exact; «تعداد» → legitimate two-table tie → `SCH.AMBIGUOUS`.

## 12. Scoring Policy

Centralized in `RetrievalPolicy` — all §19 weights unchanged (qualified 10, exact 6, case-insensitive 5, label 4, description 3, semantics 3, column-match-table +5; assist factor 0.5 cap 6 gated at qualified strength ≥ 10). Phase 8's "not all fields equivalent" is implemented as **routing** (§8); per-group weight *differentiation* beyond routing is deliberately deferred to the QuestionAnalyzer sprint (tuning without a real analyzer would be speculative — documented, not silently skipped).

## 13. Thresholds

`min_table_score=4.0`, `min_column_score=3.0` — unchanged, centralized, pinned by tests (weak "headers" match: table description token 3.0 < 4.0 → empty context).

## 14. Top-K

`table_top_k=3`, `column_top_k_per_table=6`, `max_join_path_edges=2` — unchanged; enforced on every path including the 200×50 synthetic snapshot (§27). `column_top_k_per_table` never counts force-included join endpoints.

## 15. Context Bounds

New hard limits (before the LLM boundary, never relying on prompt limits):

| Bound | Default | Binding behavior |
|---|---|---|
| `max_join_hints` | 12 | canonically-first hints kept |
| `max_semantic_entries` | 8 | canonical (snapshot) order kept from the front |
| `max_context_chars` | 8000 | deterministic budgeting (§16) |

Defaults never bind for the default top-Ks on this catalog (verified: largest evaluation context 1321 chars). Size measured by the public `serialized_context_size()` — canonical JSON (sorted keys, compact separators, `ensure_ascii=False`), the catalog's convention. **No token estimate is computed** (no repository estimator exists; provider-specific token semantics not invented — documented).

## 16. Context Budgeting

Documented priority (deterministic, never random): (1) semantics beyond the cap; (2) remaining semantics in reverse canonical order; (3) non-endpoint columns lowest-score-first, ties → lexicographically first table, then highest ordinal, then first column name. Never removed: selected tables, join hints, join-endpoint columns, warnings — a relationship is never truncated leaving one endpoint unexplained, a hint never split from its endpoints. The budgeted quantity is the context content; every iteration measures the full candidate **including the notice it will carry**, so the delivered context respects the budget whenever the structural floor allows; the notice reports the content size and what was dropped. If even the floor exceeds the budget, the floor is kept and the breach is reported — never silently unbounded. All three behaviors are test-pinned (semantics-first, column-priority with verified scores — e.g. `status` scores 9 via its description token and correctly survives longer than exact-match columns — and the 60-char floor-breach case).

## 17. Ambiguity Handling

`SCH.AMBIGUOUS` semantics unchanged (§19 of the architecture doc): material top-score ties without explicit table-name evidence, or ≥2 qualified-name matches. Preserved by the routing change (metric description evidence still breaks the OrderItems/Shipments tie); the filter-routing change *creates* the correct warning where generic filter evidence previously silenced it. Ambiguity remains a context warning — never a fallback to the full snapshot.

## 18. No-Relevant-Schema Handling

Unchanged and re-pinned: empty `SchemaContext` (`confidence=0.0`) + `SCH.NO_RELEVANT_SCHEMA` for below-threshold specs and for signal-less specs (distinct telemetry outcome `no_match`). **`NO_RELEVANT_SCHEMA ≠ SEND_FULL_SCHEMA`** — no code path in the retriever, renderer or Orchestrator seam appends snapshot content to a failing retrieval (pinned by architecture + seam tests).

## 19. SchemaContext Lifecycle

Request-scoped: produced by the retriever per request from the catalog's current snapshot, written to `AgentState.schema_context` **only** by the Orchestrator via the existing applier (`SCHEMA_RESOLVED` transition), never persisted, never cached across requests (concurrency tests prove isolation), never placed in AgentState alongside the snapshot (contract §8 unchanged).

## 20. LLM-Facing Boundary

Three distinct representations are now explicitly separated (Phase 25): `SchemaSnapshot` (canonical, catalog-owned) ≠ `SchemaContext` (request-scoped bounded selection, retriever-owned) ≠ LLM schema payload (`SchemaContextRenderer` output, renderer-owned formatting of the context). Ownership: catalog → truth; retriever → selection; context → request subset; renderer → representation. No LLM-facing component bypasses the bounded context once retrieval is active (seam leakage tests; renderer type rejection of snapshots; LLM-package import prohibition).

## 21. FULL_SCHEMA Transitional Status

**Kept, untouched, documented.** Role: prompt-truth embedded by `src/core/prompts.py` into `sql_generation_prompt` (1,946 chars, the known-drifting legacy source). The dual-schema path (legacy prompt + dormant bounded context) is documented here, in ADR-017, in the renderer docstring and in architecture doc §20.10. Retirement still requires: measured retrieval parity, SQL-generation regression evaluation, explicit fallback policy, and an authorized prompt-migration stage — none started (stop rule honored).

## 22. Full-Schema Leakage Prevention

Executable, all green:

* Whole-tree AST test: `src.core.database.models` (FULL_SCHEMA's home) is imported **only** by `src/core/prompts.py` — any new importer fails the suite.
* The retriever, renderer and Orchestrator must never reference `FULL_SCHEMA` (parametrized source test; the renderer also keeps the literal out of its own docstring).
* Renderer rejects non-context input (snapshot → typed `SchemaError`); renders nothing beyond the context (no-invention test).
* Runtime seam tests: with a snapshot containing decoy tables, the SQLGenerator receives exactly the retrieved context (object-identical to `state.schema_context`); unselected tables never appear; no legacy text crosses the generation boundary.
* LLM infrastructure (`src/core/llm/*`) must not import schema sources (AST test).
* **Dataset metric: full-schema leakage rate = 0%** over the protected rendering path across all evaluation cases (executable in `TestRetrievalMetrics::test_full_schema_leakage_rate_is_zero`), plus the runtime SQLGenerator-seam test for the generation boundary.

## 23. Telemetry

`schema_retrieval_completed` (the component-contract §3 event, emitted by the retriever): outcome (`completed`/`no_match`/`ambiguous`), `selected_tables`, `selected_columns`, `join_hints`, `semantics_entries`, `context_chars`, `confidence`, `bounded`, `snapshot_version`, `warning_codes` (codes only), `duration_ms`. Tests pin: one event per retrieval, counts correctness, code-only warnings, outcome mapping, and that a distinctive spec term never appears in the payload. No raw questions, prompts, schema text, row data or credentials are logged anywhere in the retrieval path.

## 24. Runtime Metadata

`RuntimeMetadata` gains **one additive field**, `schema_metrics: dict[str, int]` (counts + `context_chars`), plus `with_schema_metrics` on `RuntimeMetadata` and an `AgentState` applier — the existing telemetry model extended, not a second one. Written **only** by the Orchestrator in `_run_schema_resolution` when retrieval runs (mirrors the `with_llm_call` pattern; single-writer scan green); `to_dict` serializes it additively; empty on the live path while the seam is dormant (pinned). No provider-specific usage semantics invented; no token estimation.

## 25. Evaluation Dataset

`tests/test_schema/fixtures/retrieval_cases.json` — 15 cases, each with a typed spec (never raw question text) and exact expectations, run against the **real** catalog-built snapshot (checked-in metadata fixture + repository annotations; no live DB): orders-only, orderitems-only, shipments-only, Orders+OrderItems, OrderItems+Shipments, three-table, quantity ambiguity, revenue semantics, Jalali date filter, irrelevant-column, Persian label positive («راننده»), Persian label ambiguity («تعداد»), weak semantic match ("headers"), dangling reference (`buyerId` — proves no fabricated join), no-signal. Every case defines expected tables, exact columns, required joins, ambiguity state and emptiness. All 15 pass.

## 26. Retrieval Metrics

Dataset-level (executable, `TestRetrievalMetrics`): **table precision 1.0**, **table recall 1.0**, **column precision 1.0**, **column recall 1.0**, **relationship recall 1.0** (2/2 required joins), **irrelevant-table rate 0.0**, **irrelevant-column rate 0.0**, **bounded-context compliance rate 1.0** (every non-empty context within policy limits and size budget), **full-schema leakage rate 0.0** over the protected rendering path. Precision is enforced against exact truth — the retriever is not tuned for aggregate recall at the cost of irrelevant selections.

## 27. Performance

Measured (20-run averages, same process): 50×20 synthetic snapshot (1,000 columns) → **~14.7 ms/retrieval**; 200×50 (10,000 columns) → **~105.7 ms/retrieval**; canonical serialization **~0.009 ms/call**. Context in both cases: 3 tables / 5 columns / **508 chars** — payload size constant while metadata grows 10×, i.e. retrieval cost scales with schema metadata (linear column scoring) and payload size with the bounded context only. Tests assert generous bounds (< 5 s at 200×50; < 2 s per 1,000 serializations) with context-boundedness pinned; no premature optimization, no vector infrastructure.

## 28. Concurrency

16 threads × repeated mixed-spec retrieval over one shared snapshot: every result equals the serial result (no cross-request bleed); `RetrievalPolicy` frozen (mutation raises) and `DEFAULT_RETRIEVAL_POLICY` unaffected by runs; contexts are frozen value objects — cross-request mutation attempts raise. No shared mutable retrieval state exists (the retriever holds only the policy); no question-level result cache was introduced (per Phase 22).

## 29. Security Boundary

No retrieval component executes SQL or touches a driver: `src/core/schema/**` forbidden-import matrix (15 roots × each file, now including `renderer.py`), approved `pymssql` sites unchanged, fresh-subprocess footprint probes clean for catalog/retriever/renderer, import-time side-effect probes clean for all three. JoinHints remain typed metadata (no SQL text). User text is only ever compared against metadata strings; nothing from a question can become SQL in this layer. SQL security, execution policy and executor suites ran unchanged and green.

## 30. AgentState Boundary

`AgentState.schema_context` remains written only by the Orchestrator via `with_schema_context`. The single-writer AST scan is green including the new modules. The only state-surface change is additive observability (`RuntimeMetadata.schema_metrics` + applier + `to_dict` key), Orchestrator-written, mirroring the existing `with_llm_call` pattern — no new mutation *path* outside the Orchestrator, no ownership change. `state_to_api_response` and `/api/v1/ask` shape untouched.

## 31. Architecture Enforcement

Executable (all green): schema-package forbidden-import matrix + explicit no-driver rule + whole-`src.*` import allowlist (auto-extended to `renderer.py`); approved driver sites; canonical VO ownership; fresh-subprocess footprint for catalog/retriever/renderer/metadata; audit-hook import-side-effect targets (+renderer); the new `TestFullSchemaLeakagePrevention` (§22); single-writer `with_*` scan; package cycle detection (whole tree, covers the new module); renderer isolation test (imports ⊆ `{src.agents.contracts}`, no retriever import). **No whitelist was created or widened.**

## 32. Test Coverage

New/extended, all passing without a live database:

* **Hardening (22)**: filter/metric/entity routing (4), bounds & budgeting (8 — caps, semantics-first, column-priority, floor breach, determinism, snapshot-size independence, defaults-non-binding), serialized size (4), telemetry (5), plus floor-breach reporting.
* **Renderer (14)**: rendering/order/determinism (4), input contract (3), hard size limit tiers (7).
* **Determinism/concurrency/performance (14)**: repeated runs ×4 specs, `PYTHONHASHSEED` subprocess (seeds 0/1/42/12345), label-dict order, equal-score ties, duplicate relationships, 320 concurrent retrievals isolated, policy immutability, context non-mutation, large-snapshot bound, serialization latency, context-size constancy across catalog growth.
* **Evaluation (21)**: 15 exact cases + precision/recall/irrelevant-rates/relationship-recall/compliance/leakage.
* **Seam (+5)**: metrics recorded when active / absent when dormant / counts-only; SQLGenerator receives the retrieved bounded context only (2 leakage tests).
* **Architecture (+11)**: renderer footprint + isolation + FULL_SCHEMA prohibition + models-import allowlist + LLM-package prohibition + side-effect probes.

## 33. Regression Results (exact)

| Command | Result | Δ vs baseline |
|---|---|---|
| `uv run python -m pytest tests -q` | **1064 passed, 356 skipped, 0 failed** (112.80 s) | **+89 passed, ±0 skipped, 0 failed** |
| `uv run python -m pytest tests/test_schema -q` | 174 passed | +76 |
| `uv run python -m pytest tests/test_agents tests/test_architecture -q` | 692 passed, 356 skipped | green, skips unchanged |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean | — |

**Exact delta reconciliation** (+89): 71 = four new test files (22 hardening + 14 renderer + 14 determinism + 21 evaluation); +5 seam tests; +10 `test_schema_boundary.py` (+1 footprint target, +7 leakage class, +2 auto per-file matrix instances for `renderer.py`); +3 `test_boundary_hardening.py` probes (1 new target × 3). 0 failed; nothing hidden, skipped, xfailed or weakened; no existing assertion was relaxed. The 356 skips are the unchanged by-design protocol-matrix pairs.

## 34. Documentation Changes

Architecture doc §20 (new: audit matrix, routing, bounds, budgeting, semantic retrieval, confidence/fallback, telemetry/metadata, activation status, rendering boundary, transitional status/leakage/evaluation/performance); contracts doc §13; dependency-rules §3.3 note + new §3.3a; package-architecture tree; ADR-017 Phase 3.2 addendum; interface-design §7.4 (`SCH.CONTEXT_BOUNDED` registered); component-contracts §3 (error, telemetry payload, output bounds). Documentation reflects implemented behavior only.

## 35. Deviations

No architectural deviation from the approved Phase 3.2 design. Implementation decisions worth recording (all in ADR-017's addendum):

| # | Design statement | Implementation | Reason |
|---|---|---|---|
| D1 | "Maximum serialized schema-context size" | The budget governs the context **content**; each trimming iteration measures the full candidate including the notice it will carry, and the notice reports the content size | the notice is produced by the bound itself; measuring it circularly against the budget would be self-defeating; delivered contexts respect the budget whenever the floor allows (test-pinned) |
| D2 | "Distinguish metric ≠ filter ≠ entity evidence" | Implemented as **surface routing** (filters column-scoped; metrics/entities dual-surface); per-group weight multipliers deferred to the QuestionAnalyzer sprint | tuning weights without a real analyzer is speculative (AGENTS.md rule 5); routing is the testable, behavior-changing distinction that is provable now |
| D3 | Renderer docstring | Keeps the literal `FULL_SCHEMA` token out of its own source | the source-level prohibition test (§22) is substring-based; the transitional story lives in the docs |
| D4 | Production-gap fixes inside scope | removed a duplicated `_score_all_columns()` call; corrected the stale "≥ 8" assist-gate docstring to ≥ 10 | concrete defects found by the Phase 1 audit; smallest safe fix, behavior-preserving |

## 36. Remaining Migration Debt

* **QuestionAnalyzer prerequisite** (unchanged, the phase-defining debt): retrieval stays dormant; activation + production weight tuning for metric/filter differentiation land with the analyzer sprint.
* Pre-existing, untouched: `FULL_SCHEMA` prompt dependency (retires at the authorized prompt-migration stage); column-map pipeline + static schema exports deprecated-in-place; task-bound `LLMClient` seams; zero token `Usage` wiring; stray `scripts/src/` copy.
* Token-budget bound not implemented (no repository estimator — documented §15); revisit if a real consumer appears.

## 37. Known Limitations

* All retrieval behavior is exercised via injected specs; zero live-path effect until the analyzer lands (by design, test-proven).
* Semantic matching remains literal (names/labels/description tokens): Persian synonyms without a label hit (e.g. «حواله») retrieve nothing — vocabulary is the QuestionAnalyzer layer's responsibility.
* Defaults (`max_context_chars=8000`, `max_semantic_entries=8`, `max_join_hints=12`) are calibrated far above what the default top-Ks can produce on this catalog; they bind only on reduced policies or materially larger annotation sets (each behavior is tested with reduced policies).
* The renderer is dormant; the legacy prompt path remains the only live LLM-facing schema surface until prompt migration.
* Retrieval latency on very large catalogs is linear in metadata size (measured ~105 ms at 10k columns) — bounded, not sub-linear; acceptable at this scale and unoptimized by instruction.

## 38. Final Status

**PASS WITH DEBT** — every Definition-of-Done item verified with executable evidence: relevant-table selection works (eval cases 1-6, 13); relevant-column selection works (cases 7-9, 11, 14); relationship-aware retrieval works (cases 4-6, joins protected under budgeting); deterministic retrieval works (hash-seed, dict-order, tie, duplicate, concurrent and repeated-run proofs); semantic retrieval works (Persian labels, descriptions, semantics); retrieval is bounded (top-Ks + hint/semantics/size caps); relationship endpoints survive truncation; ambiguity is explicit (`SCH.AMBIGUOUS`, cases 7, 12); no-relevant-schema is explicit (cases 10, 13, 15); no relationships are fabricated; `SchemaContext` is request-scoped; concurrent retrieval is isolated; retrieval is DB-free, LLM-free, AgentState-free; downstream LLM-facing paths consume only the bounded context where retrieval is active (runtime seam proof); full-schema leakage is measured and **0%** on protected paths; `FULL_SCHEMA` is not bypassing the retrieval boundary (import surface pinned); retrieval metrics are implemented (§26); evaluation cases exist (§25); performance is bounded (§27); architecture tests pass; security/execution suites pass unchanged; full regression passes (+89 reconciled); compileall passes; documentation matches implementation; this report is complete. The debt qualifier covers the dormant integration (Case B) — an explicit, tested, documented prerequisite — not a defect of the delivered boundary.

| Question | Answer |
|---|---|
| Runtime behavior changed | **NO** (seam dormant on every live request; proven by tests; +89 tests all green with unchanged skips) |
| Public API changed | **NO** (`/api/v1/ask`, `AskRequest/AskResponse`, `execute_query`, CLI, evaluation unchanged; `AgentState.to_dict` gained one additive observability key) |
| Security behavior changed | **NO** (Security Core/execution path untouched; retrieval/renderer are metadata-only; boundary tests extended and green) |
| Database execution behavior changed | **NO** (no retrieval component touches a driver or executes SQL; driver-site allowlist unchanged) |
| AgentState ownership changed | **NO** (Orchestrator sole writer; one additive Orchestrator-written observability field on `RuntimeMetadata`) |
| Observability behavior changed | **ADDITIVE ONLY** (new `schema_retrieval_completed` event + `schema_metrics` + `schema_resolution` timing, all only when retrieval runs — i.e., not on the live path; existing events untouched) |
| LLM behavior changed | **NO** (provider boundary untouched; LLM package prohibited from schema imports by test) |

## 39. Recommendation for Next Phase (plan only — not started)

1. **QuestionAnalyzer & Active Schema Resolution** — the single blocking prerequisite: define the QuestionSpec population contract (LLM-assisted via `LLMRouter`, task `question_analysis`, deterministic post-processing; `QA.*` codes), implement behind the existing `OrchestratorDeps` seam, activate the (now fully hardened) retrieval path, tune metric/filter weight differentiation with real analyzer data, and extend the boundary-test matrix to the new module.
2. Only afterwards, a **separately authorized prompt-migration stage**: wire `SchemaContextRenderer` into SQL generation, measure retrieval parity, evaluate SQL-generation regression, then retire `FULL_SCHEMA`.

---

**STOP** — per the Phase 3.2 critical stop rule, this stage ends here. QuestionAnalyzer, prompt migration, `FULL_SCHEMA` retirement, SQLGenerator/QueryPlanner redesign, answer generation, memory changes and optimization have **not** been started and require explicit authorization based on review of this report.
