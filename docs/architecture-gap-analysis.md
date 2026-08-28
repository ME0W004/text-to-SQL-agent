# Architecture Gap Analysis — Current vs Target Capabilities

> Classification: **Missing** · **Weak** · **Partial** · **Adequate** · **Strong**.
> "Current" = code at commit `e6b53d6`. Target = the capability list for the future production-grade system.

---

| # | Capability | Rating | Why (evidence-based) |
|---|---|---|---|
| 1 | Schema Intelligence (live introspection, drift detection, coverage of all tables) | **Weak** | Static hand-written `FULL_SCHEMA` missing Shipments; live introspection exists only as an MD5 hash for column-map refresh; phantom Buyers reference; three drifting schema sources (`database/models.py`, `schema_hash.py`) |
| 2 | Semantic Layer (business metrics, measures/dimensions, currency/unit policy) | **Missing** | Only implicit conventions inside prompt text (`quantity*unitPrice`); no definitions anywhere; currency/unit/status hazards unaddressed |
| 3 | Question Understanding (intent, coreference, time expressions) | **Weak** | Raw last-5 history appended to one prompt; no parsing/normalization; observed follow-up failure («داده های که بیشتر از 50 درصد» → empty) in stored conversations |
| 4 | Query Planning (table path selection, decomposition, pre-SQL checks) | **Missing** | Single-shot LLM generation; no planning stage exists |
| 5 | SQL Generation (dialect-correct, constrained) | **Partial** | Works for common patterns thanks to rules+few-shots; but no output contract enforcement, stale few-shot year, Shipments blind spot, single provider/model |
| 6 | SQL Parsing (AST-level analysis) | **Weak** | sqlglot used only pass/fail; parsed AST discarded; multi-statement strings accepted by parser and not re-checked |
| 7 | Security Validation (statement allowlisting, injection/mutation guards) | **Weak** | SELECT/WITH prefix + 8-word textual blocklist; empirical TRUNCATE multi-statement bypass; nothing enforced at connection layer |
| 8 | Schema Validation (tables/columns existence) | **Missing** | No verification against DB; hallucinated columns become raw runtime errors |
| 9 | Semantic Validation (aggregates vs filters, units, currencies, cancelled orders) | **Missing** | Nothing; aggregate-in-WHERE failure observed unrepaired |
| 10 | Cost Validation (row limits, timeout budgeting, complexity caps) | **Missing** | Unbounded fetchall executed (`SELECT *` seen in logs); fixed 5 s query timeout is the only guard and mis-calibrated |
| 11 | SQL Repair (targeted, context-rich loops incl. execution errors) | **Weak** | One retry, validation-only, schema-less repair prompt |
| 12 | Safe Execution (read-only session, timeouts, row caps, isolation per request) | **Weak** | Single shared cursor/connection; params unused; driver-level read-only not enforced; execution errors untyped |
| 13 | Result Analysis (shape/type detection, empty-vs-error distinction, outlier checks) | **Missing** | Rows passed through untouched except renaming; empty handled with a fixed string |
| 14 | Column Selection (relevance-based projection for display) | **Missing** | Blanket `*Id` removal + static map only; selection delegated to answer LLM seeing ≤5 rows |
| 15 | Translation Metadata (grounded Persian labels incl. aliases/measures) | **Weak** | INFORMATION_SCHEMA-name map only; wrong `mesc` entry; aliases untranslated (2.5k warnings/day); auto-update writes to wrong path |
| 16 | Answer Generation (grounded, structured, complete-data narrative) | **Partial** | Fixed 4-section Persian Markdown works when data small; >5-row truncation + 500-token cap invite fabrication; heading-language inconsistency |
| 17 | Answer Validation (format check, numeric grounding check) | **Missing** | Output rendered verbatim; no post-checks |
| 18 | Context-aware Memory (sessions, pruning, token budgets, correct ordering) | **Weak** | SQLite store works; but tie-ordering corruption, no index, no cleanup, history token bloat, CLI bypasses sessions entirely |
| 19 | Few-shot Retrieval (dynamic, question-similar examples) | **Missing** | Five hard-coded examples always included |
| 20 | Model Routing (task→model mapping, fallback chains, cost tiers) | **Missing** | One model/temp/token triple for all tasks; documented fallback absent from code; 3 dead provider clients |
| 21 | Observability (request tracing, stage events, timings, token accounting) | **Missing** | Logs contain final SQL + warnings/errors only; no ids/timings/prompts; 97% warning noise; DEBUG-only execution details |
| 22 | Evaluation (regression dataset, harness, metrics) | **Missing** → baseline created this sprint | Zero tests existed; `evaluation/baseline_questions.json` + docs now provide the starting corpus, but no runner/harness yet |

## Distribution summary

- Missing: 11 capabilities (Semantic Layer, Planning, Schema/Semantic/Cost validation, Result Analysis, Column Selection, Answer Validation, Few-shot Retrieval, Model Routing, Observability, Evaluation-harness)
- Weak: 7 (Schema Intelligence, Question Understanding, Parsing, Security Validation, Repair, Safe Execution, Translation Metadata, Memory) — note: 8 items rated Weak
- Partial: 2 (SQL Generation, Answer Generation)
- Adequate/Strong: 0

## Reading of the gap

The system's center of gravity is a **single LLM round-trip wrapped in thin I/O layers**. Nearly every target capability that requires *deterministic engineering* around that round-trip (validation depth, planning, semantics, observability, evaluation) is absent or embryonic, while the two LLM-dependent stages are functional-but-fragile. This matches the Sprint 0 evidence: failures observed in practice are exactly at the boundaries the ratings mark Missing (execution-time semantic errors, silent wrong numbers, unreconstructable requests).

## Implications for sprint sequencing (not an implementation plan)

1. Safety-critical gaps (Security Validation, Safe Execution) block any production posture regardless of features.
2. Schema Intelligence + Semantic Layer unlock most correctness complaints documented in current-problems.md.
3. Observability + Evaluation are prerequisites for safely refactoring everything else (no regression net exists today).
