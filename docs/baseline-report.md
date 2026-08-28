# Sprint 0 Baseline Report

> Consolidated verdict of the discovery sprint. Every claim is backed by the detailed audits under `docs/current-system/` and by live inspection (DB, logs, SQLite memory) performed on 2026-08-25 against commit `e6b53d6`.

---

## 1. What exists today

A working MVP pipeline for Persian NL→T-SQL over three `Sale` tables (~72k rows total, MSSQL 2022):

- **Three entry points** — CLI (`run.py`), FastAPI (`/api/v1/ask`, `/health`), Streamlit chat UI calling the API.
- **One LLM path** — OpenAI-compatible endpoint (`gpt-4o-mini`, temp 0.1, 500 tokens; base URL overridable to a proxy).
- **SQL lifecycle** — schema+rules+few-shot prompt → single call → markdown/prefix cleaning → SELECT/WITH prefix check + 8-keyword blocklist → sqlglot tsql parse → one validation-repair retry → execution via pymssql → fetchall.
- **Result path** — drop `*Id` columns → static Persian column map → LLM-generated 4-section Persian Markdown answer (+ raw DataFrame in UI).
- **Memory** — SQLite sessions/messages; last-5-message history into the SQL prompt only.
- **Aux mechanisms** — MD5 schema-hash change detection triggering an LLM-driven column-map regeneration script.

## 2. What works reasonably well

- End-to-end happy path: analytical questions about Orders×OrderItems revenue return correct-looking SQL and readable Persian answers (log evidence of TOP/GROUP BY queries succeeding; report.md test 1; stored conversations).
- Dialect rules embedded in the prompt are genuinely correct and valuable (TOP vs LIMIT, `jalaliYearNo`, `operationMonth`, join keys, HAVING guidance).
- Validation reliably rejects explicit destructive intents (DELETE request blocked — report.md test 2) and catches syntax errors with a repair attempt.
- The API error-envelope design (`success=false` at HTTP 200) keeps the UI functional on failures.
- RTL handling in the UI is thoughtful (code kept LTR).

## 3. What is fragile

- **Everything around the single LLM call**: no output contract enforcement, lossy cleaning that can mutate statement type, repair loop that is context-starved and blind to execution errors.
- **Startup side effects**: agent construction may spawn a subprocess that calls the LLM and rewrites source files — which lands in the wrong directory (stray `scripts/src/core/column_mapper.py` committed to git proves it fired).
- **Concurrency posture**: async handlers run blocking LLM/DB work on a shared cursor/singleton; today's single-user usage masks serialization and race hazards.
- **Observability**: a successful request cannot be reconstructed from logs; 97% of log volume is one repeating presentation warning.
- **Ordering in memory**: same-second timestamps already corrupt turn order in the live DB.

## 4. What is missing

Schema intelligence beyond a static text block (Shipments absent); any semantic layer (currencies, units, VAT, cancelled orders); question understanding beyond raw history dumping; planning; schema/semantic/cost validation; result analysis and column selection; answer validation; few-shot retrieval; model routing/fallback; observability instrumentation; evaluation harness (baseline corpus now exists but no runner); tests entirely (empty scaffolding).

## 5. Most critical architectural weaknesses

1. **No deterministic safety net between the LLM and the database** — validation is two string checks plus a parse; empirically bypassable (multi-statement TRUNCATE passes).
2. **God-object orchestration** (`TextToSQLAgent`) coupling wiring, sessions, presentation, persistence, and error mapping — every future fix fights this file.
3. **Three drifting copies of schema truth** (live DB vs FULL_SCHEMA text vs generated COLUMN_MAP) with no reconciliation loop that actually works end-to-end.
4. **Single-provider LLM dependency with false availability logic** (pings api.openai.com even when a proxy base URL is configured; documented fallback does not exist).
5. **Runtime state scattered across CWD-relative files** (SQLite DB, logs, schema cache, generated code) — deployment-hostile.

## 6. Highest-risk correctness issues

- C1: silently wrong business numbers (currency-mixed/unit-mixed SUMs; cancelled orders counted) — undetectable by users.
- S2/N1: common failure classes unrecoverable — execution errors not repaired; Shipments questions answered blind (schema missing from prompt).
- C2/M2: history ordering corruption degrading multi-turn answers.
- S7: few-shot teaching year '1403' that cannot exist in data.

## 7. Highest-risk security issues

- X1/S1: validator bypass class — trailing statements using non-blocklisted keywords execute after a benign SELECT prefix (proven empirically). Combined with a single unsegmented DB login (X5) this is the top production blocker.
- X2: model/DB-controlled HTML rendered with `unsafe_allow_html=True` in the chat — stored XSS / prompt-injection surface.
- X4: `.env.example` committed with concrete infrastructure identifiers instead of placeholders.

## 8. Highest-risk memory issues

M1 missing index (planned then commented out), M2 second-granularity ordering ambiguity (already manifest), M3 orphaned server-side sessions forever (UI reset is client-only), M6 non-atomic per-turn writes, M7 history token bloat feeding Markdown answers back into prompts.

## 9. Highest-risk prompt issues

P2 >5-row truncation inviting fabricated tables; N1 Shipments blindness; P1 self-contradictory heading language producing mixed-language answers; P4 triplicated rule knowledge already drifting ('1403'); P5 context-free translation prompt having produced the shipped wrong label (`mesc`=«مقدار»).

## 10. Most important technical debt

- Dead mass: three provider clients + their SDK chains, unused exception hierarchy, unused write path, compact-schema exports, empty `src/schemas/`, ~9 zero-import dependencies (three of them other databases' drivers).
- No tests/no tooling config despite scaffolded test packages.
- Duplicate exception systems and duplicate logging systems.
- Runtime code generation as an update mechanism.
- Documentation (README, report.md) asserting capabilities that don't exist — actively misleading.

## 11. Recommended implementation order (direction, not plan)

1. **Safety floor** — real SQL guard (AST statement allowlist, multi-statement rejection, row caps, read-only connection posture) + regression sentinels q089–q094. Unblocks everything else.
2. **Evaluation & observability harness** — automated baseline runner + structured per-request events/timings; makes all later sprints verifiable.
3. **Decompose the agent** behind typed interfaces (R4–R7) while behavior-neutral, enabled by (2).
4. **Schema intelligence** — generated, complete (incl. Shipments), single-source schema artifact feeding prompts and validators; fix or retire the column-map autoupdate path.
5. **Semantic layer** — named metric definitions (revenue/VAT/currency/delivery-ratio) used by generation and validation.
6. **Question understanding + memory correctness** — turn ordering keys, atomic writes, budgets, coreference strategy.
7. **Repair loops extended to execution errors**, with schema-grounded repair prompts.
8. **Model routing/provider abstraction cleanup** — delete dead clients or wire them properly behind one interface.
9. **Presentation layer** — grounded answer generation over full (budgeted) data, alias-aware translation metadata, single rendering of tables, XSS-safe rendering.
10. **Hygiene sweep** — dependency pruning, doc/code reconciliation per `discrepancies.md`.

Sprint 0 stops here. No implementation was performed; repository behavior is unchanged except for added documentation and evaluation artifacts.
