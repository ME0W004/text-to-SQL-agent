# Engineering Rules — For All Future Implementation Sprints

> Binding rules derived from Sprint 0 findings. Each rule exists because violating it already caused a documented problem (references to current-system docs).

---

## 1. Behavior preservation

1. **R1 — No silent behavior change.** Do not break existing working behavior (e.g., successful analytical flows, DELETE rejection) without explicit justification recorded in the sprint doc.
2. **R2 — Incremental refactoring only.** Replace one responsibility at a time behind stable seams; no big-bang rewrites. The god-object `text_to_sql_agent.py` decomposes stepwise, not wholesale.
3. **R3 — Preserve public API contracts** (`/api/v1/ask`, `/api/v1/health` request/response shapes incl. the `success=false` + HTTP 200 envelope) unless a contract change is explicitly approved; if changed, update Streamlit client in the same commit.

## 2. Structure & typing

4. **R4 — Single responsibility per module.** Orchestration, session management, presentation transforms, and persistence must live in separate units (evidence: current agent mixes six roles).
5. **R5 — No hidden global state.** Module-global singletons (`routes.agent_instance`) are prohibited in new code; dependencies are constructed/injected explicitly with defined lifecycles.
6. **R6 — Typed interfaces everywhere.** Public functions take/return typed models (Pydantic/dataclasses), not loose dicts. Result envelopes get a typed schema shared by API/UI/tests.
7. **R7 — One exception hierarchy.** Use `utils/exceptions.py` (deduplicated); raise specific types at boundaries; never duplicate exception classes per module; never wrap-and-destroy types into bare `RuntimeError` without cause.

## 3. Correctness & safety

8. **R8 — Test before refactoring when practical.** A capability may be refactored only after it is covered by the baseline evaluation or a unit test; new bug fixes require regression tests (`evaluation/expected-behavior.md` regression rule).
9. **R9 — Validation must not depend solely on LLM instructions.** Deterministic code (parser/AST checks) enforces structure; prompt rules are hints, not guarantees (evidence: aggregate-in-WHERE failure).
10. **R10 — Security enforced outside the LLM.** Statement allowlisting, multi-statement rejection, row caps, and read-only execution must be deterministic layers independent of model compliance (evidence: TRUNCATE bypass).
11. **R11 — Database access stays isolated** behind a single data-access module; no SQL strings built outside it; every query has an explicit timeout and a row limit policy; connection-per-request or a properly guarded pool — never a shared cursor across requests.
12. **R12 — Explicit error handling.** Every I/O boundary catches known failure classes and maps them to typed errors; generic `except Exception` allowed only at the outermost boundary; no bare `except:`.
13. **R13 — Fail loudly at startup, gracefully at runtime.** Configuration/provider problems should be detected by an explicit startup check, not discovered inside the first user request.

## 4. Data & semantics

14. **R14 — Single source of truth for schema knowledge.** Schema descriptions are generated from live metadata (or one canonical artifact) — never three drifting copies (evidence: FULL_SCHEMA vs DB vs COLUMN_MAP).
15. **R15 — Semantic definitions are code, not folklore.** Business metrics (revenue, VAT handling, currency normalization, delivery ratio) are implemented as named, testable functions referenced by generation/validation.
16. **R16 — Respect data reality.** Duplicate Shipments Ids, mixed units/currencies, nullable measures are facts any feature must account for; "it works on clean data" is not acceptance.

## 5. Memory & context

17. **R17 — Memory writes are atomic per turn** (user+assistant together) and ordered by a monotonic key, not second-granularity timestamps.
18. **R18 — Context budgets are explicit.** Anything injected into prompts has a token budget and truncation strategy decided in code, not incidentally.

## 6. LLM usage

19. **R19 — Providers stay behind one abstraction**, implementing one interface with availability checks; routing/fallback decisions are configuration, not scattered conditionals.
20. **R20 — Prompts are versioned artifacts** with owners and evaluation coverage; changing a prompt requires running the baseline suite and recording deltas.
21. **R21 — No runtime side-effectful code generation.** The system must never rewrite its own source files during a request path (evidence: column-map regeneration writing to the wrong directory).

## 7. Hygiene

22. **R22 — Secrets never in source, docs, logs, or example files.** `.env.example` carries placeholder values only.
23. **R23 — Observability is part of done.** Any new stage emits structured events (request id, stage, duration, outcome). A sprint that cannot demonstrate its effect through logs/metrics isn't finished.
24. **R24 — Documentation updates with code.** Every PR that changes behavior updates the relevant `docs/current-system/*` audit file or moves the item from "current" to "changed" explicitly.
25. **R25 — Dead code is deleted**, not commented (evidence: unused providers/drivers/exceptions confused every audit).
26. **R26 — Dependencies earn their place.** A package with zero imports gets removed at the next dependency touchpoint.
