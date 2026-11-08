# Sprint 3.1 / Prompt 3.1C Report — SchemaRetriever & Controlled Schema Resolution

**Date**: 2026-09-04 · **Branch**: `new-ar` · **Stage**: 3.1C (implementation stage — retrieval layer + dormant seam wiring)
**Final status**: **PASS WITH DEBT** (§35; the debt is the pre-existing, explicitly documented QuestionAnalyzer prerequisite — the retrieval layer itself is complete and dormant by design)

---

## 1. Executive Summary

Stage 3.1C implemented the deterministic `SchemaRetriever` (`src/core/schema/retriever.py`) against the **unchanged** contract value objects: additive scoring over the `QuestionSpec` fields that actually exist (entities/filters/metrics), centralized `RetrievalPolicy` (weights, thresholds, top-K, join-path bound), relationship-assist expansion restricted to snapshot relationships and qualified-strength neighbors, bounded deterministic BFS join-path generation with metadata-only `JoinHint`s, and the contract-mandated `SCH.NO_RELEVANT_SCHEMA` / `SCH.AMBIGUOUS` warning semantics. The preflight audit returned **Case B** — no `QuestionAnalyzer` exists and nothing populates `question_analysis` on the live path — so the catalog/retriever seams were wired through the existing `OrchestratorDeps` fields but remain **provably dormant**: no `QuestionSpec` is fabricated, no context created, no catalog access, no stage transition, and every generation-side surface (`FULL_SCHEMA`, prompts, few-shots, extractor, API) is untouched. Supporting changes: typed `SchemaError` handling on the Orchestrator's known-failure path and one additive state transition (`SCHEMA_RESOLVED → SQL_GENERATED`, the established direct-path pattern) so the seam can complete without a QueryPlanner once activated. Full regression: **975 passed / 356 skipped / 0 failed** vs baseline 923/356/0 with the delta fully reconciled (§30); compileall clean.

## 2. Baseline Before Implementation

Measured fresh at preflight (not assumed from 3.1B):

| Command | Result |
|---|---|
| `uv run python -m pytest tests -q` | **923 passed, 356 skipped, 0 failed** (82.18 s) |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean |

Exact match with the 3.1B end state.

## 3. Current QuestionSpec Availability

| Question | Evidence-based answer |
|---|---|
| Who can produce a `QuestionSpec` today? | **Nobody.** `OrchestratorDeps.question_analyzer` is `Optional[QuestionAnalyzer] = None` and the façade never passes one; no `QuestionAnalyzer` implementation exists anywhere in `src/` (only the Protocol, docstring mentions, and the state applier). |
| What is `state.question_analysis` on the live path? | Always `None` — `_run_question_analysis` returns the state unchanged when the analyzer is `None`. |
| What did `_run_schema_resolution` do before 3.1C? | With `question_analysis is None` → returned state unchanged (dormant, no fabricated context, no transition). With a spec but no components → applied an empty `SchemaContext()` (pre-existing Sprint 2B transitional behavior, preserved). |
| Case verdict | **Case B** — implement the full retriever + tests; do NOT fabricate a spec; keep the seam dormant; document the prerequisite. |

## 4. QuestionAnalyzer Dependency Audit

* No production module imports or implements `QuestionAnalyzer` (grep-verified; the only references are `contracts.py` Protocol definitions, `orchestrator.py`'s optional dep, and docstrings).
* No test-only analyzer is imported into production; test fakes (`tests/test_agents/test_orchestrator.py`, the new seam tests) inject analyzers explicitly to exercise the active path.
* `QuestionSpec` fields populated **today**: none (nothing populates it). Fields that **only future QuestionAnalyzer work** will populate: `intent`, `entities`, `filters`, `metrics`, `time_constraints`, `references`, `ambiguity`, `confidence`, `warnings`.
* Retrieval signals **available now** (exercised via injected specs in tests): entity `text`/`qualified_name`/`entity_type`, filter `field`s, metric `name`s. Signals **future-dependent**: everything on the live path (dormant), plus conversation/time-aware retrieval (explicitly out of scope).

## 5. Implemented Files

**New**
* `src/core/schema/retriever.py` — `SchemaRetriever`, `RetrievalPolicy`, `DEFAULT_RETRIEVAL_POLICY`
* `tests/test_schema/test_retriever.py` (37 tests), `tests/test_schema/test_orchestrator_seam.py` (9 tests)

**Modified (additive / minimal)**
* `src/agents/state.py` — `VALID_TRANSITIONS[SCHEMA_RESOLVED]` gains `SQL_GENERATED` (documented direct-path pattern; §32 D2)
* `src/agents/orchestrator.py` — `SchemaError` added to the known-failure catch + a `SchemaError` branch in `_handle_known_failure` (§32 D1)
* `src/agents/text_to_sql_agent.py` — constructs `self.schema_retriever` and passes both seams into `OrchestratorDeps` via the existing `getattr` fallback pattern
* `tests/test_architecture/test_schema_boundary.py` — footprint target + auto-picked-up per-file rules
* `tests/test_architecture/test_boundary_hardening.py` — `SIDE_EFFECT_TARGETS` gains `src.core.schema.retriever`

**Documentation**: architecture doc §19 (new), contracts doc §12 (new), dependency-rules §3.3, package-architecture tree, ADR-017 3.1C addendum, this report.

## 6. Contract Changes

**None.** All retrieval work uses the already-approved definitions (`QuestionSpec`, `SchemaSnapshot`, `SchemaContext`, `TableRef`, `ColumnRef`, `JoinHint`, `BusinessSemantics`, `SchemaRetriever` Protocol, `SchemaError`). No field was added, renamed or moved; no duplicate definition exists (whole-tree canonical-ownership scan green); the protocol matrix is unchanged (356 skips identical to baseline); `runtime_checkable` conformance and signature parity are pinned by new tests.

## 7. SchemaRetriever Interface

```python
class SchemaRetriever:
    def __init__(self, policy: RetrievalPolicy = DEFAULT_RETRIEVAL_POLICY) -> None: ...
    def retrieve(self, spec: QuestionSpec, snapshot: SchemaSnapshot) -> SchemaContext: ...
```

Pure function of its inputs: no I/O, no clock, no randomness, no state; consumes an already-built snapshot; never calls `catalog.refresh()` (§19 below). Signature parity with the canonical Protocol is asserted (positional parameter sequences) and `isinstance` conformance holds.

## 8. Retrieval Signals

Used (all from existing contract fields): entity `text`, entity `qualified_name`, entity `entity_type` (routes a term to table-only / column-only / both surfaces), filter `field`, metric `name`; snapshot-side surfaces: qualified table/column names, unqualified names, `ColumnDescriptor.description`, `TableDescriptor.description`, `SchemaSnapshot.labels`, `BusinessSemantics.description`/`formula`/`applies_to`, `Relationship`s. Not used (future-dependent): `time_constraints`, `references`, `ambiguity`, `intent` (no demonstrated deterministic mapping yet — documented, not silently ignored).

## 9. Scoring Model

Additive per-object scores (weights centralized in `RetrievalPolicy`, architecture doc §19):

| Signal | Table | Column |
|---|---|---|
| Qualified-name exact | 10 | 10 |
| Exact unqualified name | 6 | 6 |
| Case-insensitive name | 5 (only if exact didn't fire) | 5 (same rule) |
| Label equality | 4 | 4 |
| Description-token overlap | 3 (**suppressed when the table was explicitly named** — same evidence, no double-count) | 3 |
| Business-semantics token | — | 3 |
| Table owns a matched column (≥ `min_column_score`) | +5 | — |

Token matching is Unicode word-token casefolding (Persian included). A qualified column reference selects its table at full qualified strength.

## 10. Thresholds

`min_table_score = 4.0` (a lone table-description token at 3.0 stays out; any name/label signal or owning-a-matched-column is in), `min_column_score = 3.0` (weak textual evidence alone reaches columns but not tables — a table gains relevance via `w_column_match_table` when it owns such a column). Both centralized; threshold behavior unit-tested.

## 11. Top-K Limits

`table_top_k = 3`, `column_top_k_per_table = 6`, `max_join_path_edges = 2`. Truncation is deterministic (score desc, then name / ordinal); enforced by tests including a reduced-policy run.

## 12. Table Selection

Score all snapshot tables (never mutate the snapshot), apply the single relationship-assist pass, filter by `min_table_score`, order by (score desc, qualified name), truncate to `table_top_k`.

## 13. Column Selection

Per selected table: score every `column_details` entry (plus a name-only pass for columns without descriptors, so hand-built snapshots remain usable), filter by `min_column_score`, order by (score desc, ordinal position, name), truncate per table; then force-include join-endpoint columns.

## 14. Relationship Expansion

One assist pass, never iterated (no feedback loops): a below-threshold table gains `min(0.5 × strongest_neighbor_score, 6.0)` **only** when its strongest snapshot neighbor reached qualified strength (≥ 10) — i.e., the question explicitly named the neighbor. Verified consequences: qualified `Sale.Orders` pulls `OrderItems` to 5; a mere exact-name match (6) does not expand; weak textual evidence (e.g. a semantics-description token scoring 8) never cascades across the join graph. Only relationships present in the snapshot participate; **no inferred relationships exist** (pinned by the dangling-`buyerId` test).

## 15. JoinHint Generation

Undirected BFS over snapshot relationships between every ordered pair of selected tables: bounded depth (`max_join_path_edges`), sorted neighbor expansion (deterministic), visited-set cycle prevention, one shortest path per pair, duplicate-edge elimination; each hint carries the relationship's canonical direction (`left=from`, `right=to`) with `kind="inner"` default. Hints are typed metadata (`ColumnRef` pairs) — **no SQL text exists anywhere in the retriever** (asserted). The documented two-hop chain `Orders ↔ OrderItems ↔ Shipments` resolves to exactly the two canonical hints; with a 1-edge bound it correctly yields none.

## 16. Ambiguity Semantics

`SCH.AMBIGUOUS` warning iff (a) the top table score is shared by ≥2 selected tables **none of which carries explicit table-name evidence** (qualified/exact/case-insensitive name), or (b) ≥2 tables are qualified-name-matched (conflicting strong evidence). Verified positives: a bare `quantity` term ties OrderItems/Shipments with no name evidence → warns. Verified negatives: naming Orders+Shipments explicitly → no warning (ordinary multi-table relevance); distinct evidence — the revenue-semantics link making `OrderItems.quantity` (12) outrank `Shipments.quantity` (9) — breaks the tie and never warns. Context still carries the selected subset (warnings, not hard rejection, per component-contracts §3).

## 17. No-Relevant-Schema Semantics

No table ≥ threshold, or a spec with no retrieval signals → an empty `SchemaContext` (`confidence=0.0`) with a `SCH.NO_RELEVANT_SCHEMA: …` warning. Never fabricated content; never raised as an exception (raise is reserved for orchestrator policy, not implemented in this stage).

## 18. SchemaContext Determinism

Same spec + snapshot ⇒ equal `SchemaContext` (equality-tested): tables by (score desc, name); columns by (score desc, ordinal, name); hints by canonical tuple; semantics preserve snapshot order (already canonical from 3.1B). No dictionary-order dependence; all ordering keys are discrete.

## 19. Catalog Interaction

The orchestrator seam is the only component that fetches the snapshot (`catalog.get_snapshot()`); the retriever receives it as a parameter and **cannot** trigger refresh (no reference to the catalog exists in the retriever module — source-checked).

## 20. Stale Snapshot Handling

Staleness is a catalog concern (3.1B): a stale-but-valid snapshot is served by the catalog and retrieved like any other — retrieval proceeds, nothing converts stale to fresh, no refresh is initiated by the retriever or the seam. When no snapshot exists, `catalog.get_snapshot()` raises `SCH.UNAVAILABLE`, which the seam propagates (§21) — an empty context is never fabricated (tested).

## 21. Orchestrator Wiring

* `_run_schema_resolution` (pre-existing seam) is unchanged in its dormant behavior and now fully functional on the active path: `catalog.get_snapshot()` → `retriever.retrieve(spec, snapshot)` → `state.with_schema_context(...)` (the existing applier + `SCHEMA_RESOLVED` transition).
* The façade constructs `SchemaRetriever()` and passes `schema_catalog`/`schema_retriever` into `OrchestratorDeps` (existing Optional fields; `getattr` fallback preserves legacy `object.__new__` test construction).
* `SchemaError` added to `run()`'s known-failure tuple; `_handle_known_failure` maps it to `ErrorCategory.SCHEMA` with the approved `code` preserved (`SCH.UNAVAILABLE`/`SCH.INTERNAL`) — consistent with the existing `QueryExecutionError` precedent.
* Additive state transition `SCHEMA_RESOLVED → SQL_GENERATED` (§32 D2) so the seam completes while no QueryPlanner is wired.

## 22. AgentState Boundary

The retriever imports no state module, constructs no state, and calls no applier (source-checked + repo-wide single-writer AST scan green). `AgentState.schema_context` is written only by the Orchestrator. The one state change is the additive transition allowlist entry (§32 D2) — it changes nothing on the live path (dormant) and only permits the documented future flow.

## 23. LLM Boundary

The retriever module contains no LLM import, provider SDK, prompt template, or `LLMRouter` reference (architecture matrix + footprint probes). Retrieval runs offline on fixtures.

## 24. Database Boundary

No driver import, no connection, no SQL construction anywhere in `src/core/schema/retriever.py` (architecture matrix + fresh-subprocess probes; import-time side-effect audit-hook target added).

## 25. Security Boundary

The retriever processes metadata only; it accepts no SQL, executes nothing, alters no policy, and bypasses nothing. `JoinHint`s are typed planning metadata, not executable text; user text can never become SQL in this layer (terms are only ever compared against metadata strings).

## 26. Memory Boundary

No `MemoryManager` import, no conversation inspection, no persistence — architecture matrix green; conversation-aware retrieval remains future work requiring explicit authorization.

## 27. Prompt / SQL-Generation Boundary

`FULL_SCHEMA`, prompt rules, few-shots, `SQLExtractor`, `SQLGenerator`, `AnswerGenerator`, execution policy, SQL security, API response shape, CLI behavior: **all byte-identical** (full regression + compatibility suites green). `SchemaContext` may now reach `AgentState.schema_context` when a spec exists, but no generation component consumes it — the prompt-migration non-goal is honored.

## 28. Test Coverage

**`tests/test_schema/test_retriever.py` — 37 tests** (matching: exact table, exact column, qualified table, qualified column reference, description token, Persian label, business-semantics, combined signals, filter/metric fields; ranking: order + tie-break, threshold exclusion, table top-K, column top-K, ordinal tie-breaking; relationships: direct hints, two-edge chain, endpoint inclusion, bounded depth (1-edge policy excludes the 2-hop chain), no inference, duplicate-row elimination, assist pull-in for qualified matches, no assist for weak matches; ambiguity: named multi-table ≠ ambiguous, `quantity` tie warns, distinct scores silent, double-qualified conflict; no-relevant-schema; context: determinism, bounded size, typing, no SQL text; failures: empty spec, `None` spec/snapshot, empty snapshot; conformance: protocol isinstance, signature parity, no second Protocol in the module).

**`tests/test_schema/test_orchestrator_seam.py` — 9 tests**: dormant seam (no spec → no context/transition/catalog-or-retriever access, pipeline still succeeds; **real façade wiring test** — the actual `TextToSQLAgent` builds catalog+retriever into deps and a full offline `ask()` leaves the seam dormant); active seam (spec → QUESTION_ANALYZED → SCHEMA_RESOLVED → ANSWERED with exactly one catalog read and one retriever call; typed, selected context); typed failures (`SCH.UNAVAILABLE` from the catalog and retriever errors → `ErrorCategory.SCHEMA` with code preserved; no empty context on failure); state ownership (retriever never touches state; context arrives only via the applier).

**Architecture**: the schema-boundary per-file import matrix, driver rule, and transitive-import allowlist automatically cover `retriever.py`; footprint probes extended with `src.core.schema.retriever`; side-effect audit-hook target added; canonical-ownership and cycle scans unchanged and green.

## 29. Architecture Enforcement

Executable: schema-package forbidden-import matrix now spans `catalog.py`, `retriever.py` (15 forbidden roots each); fresh-subprocess footprint probes for the retriever (no driver/SDK/state/LLM-boundary module loaded); CPython audit-hook import-time side-effect target; single canonical definition per schema VO (whole-tree); package cycle detection (whole-tree, covers the new module); single-writer `with_*` AST scan; protocol signature parity. No new whitelist was created anywhere.

## 30. Regression Results

| Command | Result | Δ vs baseline |
|---|---|---|
| `uv run python -m pytest tests -q` | **975 passed, 356 skipped, 0 failed** (92.50 s) | +52 passed, ±0 skipped |
| `uv run python -m pytest tests/test_schema -q` | 98 passed (52 → 98) | +46 |
| `uv run python -m pytest tests/test_agents tests/test_architecture/test_single_writer.py tests/test_architecture/test_validation.py -q` | 542 passed, 356 skipped | green |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean | — |

**Exact delta reconciliation**: +37 retriever tests, +9 seam tests, +1 boundary footprint instance (`retriever`), +1 per-file driver-rule instance (retriever.py), +2 forbidden-import matrix instances (retriever.py ×{matrix, driver} — the matrix is parametrized per file), +3 side-effect-target probes (1 new target × 3 parametrized) = **+52**. Skips unchanged (356 = 16 + 340 by-design matrix pairs; no protocol shape changed). 0 failed; nothing hidden, skipped, xfailed or weakened; no existing assertion was relaxed.

## 31. Documentation Changes

* `docs/architecture/schema-intelligence.md` — new §19: signals/weights tables, limits, assist + join-path rules, ambiguity/no-relevance semantics, determinism, wiring + dormancy status, QuestionAnalyzer prerequisite, explicit prompt-migration non-goal.
* `docs/contracts/schema-intelligence.md` — new §12: implemented retriever semantics against unchanged contracts; status header updated to 3.1B–3.1C.
* `docs/architecture/dependency-rules.md` §3.3 — implemented-as note + enforcement pointers.
* `docs/architecture/package-architecture.md` — target tree gains `schema/retriever.py`.
* `docs/adr/ADR-017` — 3.1C addendum (retriever implemented; dormant wiring; the additive transition).

## 32. Deviations

No architectural deviation from the approved design was required. Two implementation decisions beyond pure addition, both documented in ADR-017's addendum:

| # | Approved design | Discovered repository reality | Implementation decision | Compatibility effect | Documentation |
|---|---|---|---|---|---|
| D1 | "Retriever failure → verify typed failure propagation and correct state/error semantics" (Phase 26) | The Orchestrator's known-failure path did not handle `SchemaError`; an unhandled seam failure would degrade to `ErrorCategory.INTERNAL` with the code replaced by the exception class name — losing the approved `SCH.*` semantics | Added `SchemaError` to the existing known-failure tuple with a dedicated branch (`ErrorCategory.SCHEMA`, code preserved, non-retryable) — the same pattern as `QueryExecutionError` | Live path unchanged (seam dormant); only affects the future active path's error semantics | ADR-017 addendum; contracts doc §12 |
| D2 | Phase 14: "With valid QuestionSpec → the full chain runs … → SCHEMA_RESOLVED" | `AgentState.VALID_TRANSITIONS` had no `SCHEMA_RESOLVED → SQL_GENERATED` edge; with no QueryPlanner wired, an active seam could not legally reach SQL generation (the dormant-path stub never hit this because nothing transitioned) | Added the edge with the same explicit "direct path for current architecture" comment style used by the pre-existing `INITIALIZED → SQL_GENERATED` and `EXECUTED → ANSWERED` transitional exceptions | Additive allowlist entry; unreachable on the live path (seam dormant); full state-machine suite green | ADR-017 addendum; architecture doc §19 |

Neither change weakens any boundary: both are narrow, pattern-consistent, and pinned by tests.

## 33. Remaining Debt

* **QuestionAnalyzer prerequisite (the debt this stage is defined by)**: the retrieval layer is complete but dormant; activation requires the QuestionAnalyzer sprint (which must also decide the QuestionSpec population contract). No retrieval signal is wasted when it lands.
* Pre-existing (unchanged): column-map pipeline + static schema exports deprecated-in-place; `FULL_SCHEMA` prompt dependency (retires at prompt migration); task-bound `LLMClient` seams; A2 composition import; zero token `Usage` wiring; stray `scripts/src/` copy.
* `intent`/`time_constraints`/`references` are not yet retrieval signals (no demonstrated deterministic mapping; documented for the analyzer sprint).

## 34. Known Limitations

* The retriever matches literal strings (names, labels, description/semantics tokens); Persian synonyms with no label hit (e.g. «حواله») retrieve nothing — term vocabulary is the QuestionAnalyzer layer's responsibility (documented non-goal here).
* Relationship assist expands only from qualified-strength neighbors; exact-name-matched tables' neighbors are not pulled in (deliberate boundedness; a question naming one table gets exactly that table plus nothing unverified).
* `table_top_k=3` equals this database's full table count; with larger catalogs the truncation becomes meaningful (tested at K=2).
* Ambiguity detection is purely score-shape-based under the documented rules; two genuinely different tables with identical evidence shapes warn, and semantics-linked evidence silently disambiguates.
* `JoinHint.kind` is always `"inner"` in this stage; directional (LEFT/RIGHT) selection is future QueryPlanner policy.

## 35. Final Status

**PASS WITH DEBT** — every Definition-of-Done item verified: retriever implemented; deterministic, bounded, explicit tested scoring; centralized policy; snapshot-relationship-only expansion; no fabricated relationships; metadata-only JoinHints; contract-conformant ambiguity and `SCH.NO_RELEVANT_SCHEMA`; deterministic context; LLM/DB/AgentState/Memory-free; architecture, contract, targeted-schema, orchestrator, full-regression and compileall suites green; no prompt migration; no unintentional SQL-generation change; `FULL_SCHEMA` and ColumnMapper intact; documentation matches implementation; report complete. The "debt" qualifier reflects the deliberately dormant integration (Case B) — an explicit, tested, documented prerequisite rather than a defect.

| Question | Answer |
|---|---|
| Runtime behavior changed | **NO** (seam dormant on every live request; proven by tests) |
| Public API changed | **NO** |
| Security behavior changed | **NO** |
| Database execution behavior changed | **NO** |
| AgentState behavior changed | **ADDITIVE ONLY** — one transition allowlist entry (`SCHEMA_RESOLVED → SQL_GENERATED`, direct-path pattern); unreachable on the live path; ownership unchanged (Orchestrator sole writer) |
| Observability behavior changed | **NO** (no new events; existing surfaces untouched) |
| LLM behavior changed | **NO** |

## 36. Recommendation for Next Stage (plan only — not started)

**QuestionAnalyzer & Active Schema Resolution** is the natural next stage, upon explicit authorization: (1) define the QuestionSpec population contract (LLM-assisted via `LLMRouter`, task `question_analysis`, deterministic post-processing; error codes `QA.*` per component-contracts §1); (2) implement `QuestionAnalyzer` behind the existing `OrchestratorDeps` seam with bounded LLM budget and typed failures; (3) activate schema resolution (the seam, applier and transition path are ready and tested); (4) wire `RuntimeMetadata`/telemetry for the analysis stage; (5) extend the single-writer/boundary test matrix to the new module; (6) only afterwards, a separately authorized prompt-migration stage consumes `SchemaContext` and retires `FULL_SCHEMA`.

---

**STOP** — per the 3.1C critical stop rule, this stage ends here. QuestionAnalyzer, prompt migration, `FULL_SCHEMA` retirement, SQLGenerator redesign, ColumnMapper retirement, answer-generation work, memory changes and production optimization have **not** been started and require explicit authorization based on this report.
