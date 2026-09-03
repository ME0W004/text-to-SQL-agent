# Sprint 2 / Prompt 3A Report — Dependency Audit & Target Package Design

**Date**: 2026-09-02
**Stage**: 3A (design/audit only — no production refactor performed)
**Decision record**: [`ADR-016`](../adr/ADR-016-package-architecture.md)
**Design document**: [`package-architecture.md`](../architecture/package-architecture.md)

## 1. Executive Summary

The audit confirms that the Sprint 2 architectural principles hold at the
**direct-import** level (all existing architecture tests pass) but fail at
the **transitive/package** level through two confirmed doors:

- **F1 (Critical)**: `src/agents/__init__.py` eagerly imports the façade,
  Orchestrator and adapters, so importing *any* `src.agents.*` module —
  including the contract module — transitively imports 29 `src.*` modules,
  the **openai SDK**, **pymssql**, `requests`, `dotenv` and `pydantic`.
- **F2 (High)**: `contracts.py` imports `ExecutionPolicy`/`DatabaseErrorCode`
  from `src/core/database/connection.py`, which imports `pymssql` at module
  top — the contract authority can never be driver-free.
- **F3 (High)**: the Orchestrator chain transitively imports the openai SDK
  (`orchestrator → sql_extractor.extractor → llm.client → openai_client`).

No dependency cycles, no state leakage, no API/UI leakage, no upward
dependencies were found. The approved design (ADR-016): keep
`contracts.py` as the single contract authority (Option A), make
`src/agents/__init__.py` lazy (PEP 562), relocate the three driver-free
value objects to a new pure `src/core/database/policy.py` with
re-exports, and extend architecture tests to transitive footprint checks.
Nothing else moves.

## 2. Current Package/Module Inventory

See [`package-architecture.md` §1](../architecture/package-architecture.md)
— every `src` module plus `evaluation/`, `scripts/`, and the three
entrypoints is classified (public/internal, domain/application/
infrastructure/adapter/interface, cross-cutting).

## 3. Current Dependency Graph Findings

Verified edge map in [`package-architecture.md` §2](../architecture/package-architecture.md#2-current-dependency-graph-verified-edges).
Key verified facts:

- No `src/core|memory|utils` module imports `src.agents` (no upward deps).
- `src/api/routes.py` imports only the façade; `src/web/app.py` is HTTP-isolated.
- `src/core/security/sql_security.py` is pure (`sqlglot` only, no `src` imports).
- `src/memory/manager.py` is self-contained (`sqlite3`, `os`).
- `src/utils/logger.py` imports nothing from `src`.
- `evaluation/` imports the agent lazily inside `evaluation/run.py` only.
- Footprint probes (fresh subprocess, per module):
  - `import src.agents.contracts` → external: `openai, pymssql, requests, dotenv, pydantic, sqlite3, sqlglot`; 29 `src.*` modules; façade loaded.
  - `import src.agents.state` / `orchestrator` / `text_to_sql_agent` → identical footprint.
  - `import src.core.security.sql_security` → `sqlglot` only.
  - `import src.core.database.connection` → `pymssql`, `sqlglot`.
  - `import src.utils.logger` → nothing external; `src.memory.manager` → `sqlite3` only.

## 4. Confirmed Architectural Violations

| ID | Severity | Classification | Finding |
|---|---|---|---|
| F1 | Critical | **Confirmed** | Eager `src/agents/__init__.py` makes every `src.agents.*` import pull the whole stack incl. provider SDK + DB driver |
| F2 | High | **Confirmed** | Contract authority imports from a module that imports `pymssql` (`contracts.py:44-47` ← `connection.py:18`) |
| F3 | High | **Confirmed (transitive)** | Orchestrator chain loads openai SDK via `sql_extractor.extractor → llm.client → openai_client`; direct-import tests pass, spirit of rule 5 violated |
| F4 | Medium | Migration debt | Legacy `SQLValidator` wrapper + class-level mutable singleton (`sql_extractor/validator.py`) — tracked by ADR-014, removed when SQLExtractor is replaced |
| F5 | Medium | Confirmed (cosmetic) | `contracts.SyntaxError` / `contracts.MemoryError` shadow builtins; no runtime defect |
| F6 | Low | Doc drift | `contracts.py` docstring cites nonexistent `test_contracts_dependencies.py` |
| F7 | Low | Intentionally allowed | `SecurityValidatorAdapter` builds a throwaway validator per non-default-policy call |
| A1–A3 | — | Intentionally allowed | Inline security check in `execute_query`; façade as composition root; MemoryManager default path |
| D1–D2 | — | Migration debt | OpenAI-flavored `LLMClient` (LLMRouter deferred, Sprint 6); `os.getenv` in legacy components (grandfathered §4.6) |

## 5. Confirmed Dependency Cycles

**None.** `test_no_dependency_cycles_among_internal_packages` (DFS cycle
detection, no whitelist since Prompt 2C) passes on the live graph, and the
manual edge map shows `agents → core` strictly one-directional.

## 6. Migration Debt

Pre-existing, documented, unchanged by this stage: legacy
`SQLValidator` (F4/D1), `LLMClient` before `LLMRouter` (D1), direct
`os.getenv` in legacy components (D2). New debt created by the 3B design:
the permanent re-export of `ExecutionPolicy`/`DatabaseError`/
`DatabaseErrorCode` from `connection.py` after their canonical home moves
to `policy.py` (compatibility surface, identity-tested, not duplicated).

## 7. Target Package Tree

[`package-architecture.md` §4](../architecture/package-architecture.md#4-target-package-tree).
Structurally identical to today except one new module
(`src/core/database/policy.py`); the changes are import direction and
import-time weight.

## 8. Dependency Direction Matrix

[`package-architecture.md` §5](../architecture/package-architecture.md#5-dependency-direction-matrix)
— ten source layers × allowed/forbidden, covering domain, contracts,
application/orchestration, adapters, security, execution, LLM, memory,
interface, façade, evaluation. Exceptions are explicit (logger shared
surface; façade wiring role; grandfathered `os.getenv`).

## 9. Responsibility / Ownership Matrix

[`package-architecture.md` §6](../architecture/package-architecture.md#6-ownership-rules)
— where code lives, who may import/call it, the protecting abstraction,
and the migration/compat rule per responsibility.

## 10. Contracts Package Decision

**Option A — preserve `src/agents/contracts.py` as the central contract
authority. Do not split.** Evidence: single cohesive file (963 lines,
~70 names), imported by `state`, `orchestrator`, `adapters` and hundreds
of test call sites; splitting adds shim cost with zero boundary gain.
The actual boundary problem is its *import direction* (F2), fixed by
relocating the two imported names' definitions to a pure module — not by
splitting the contract file.

## 11. Compatibility Strategy

- `from src.agents import TextToSQLAgent` (and all other current
  `src.agents` exports) keep working under PEP 562 lazy `__getattr__`.
- `from src.core.database.connection import ExecutionPolicy,
  DatabaseError, DatabaseErrorCode` keeps working via re-export; the
  re-export must be identity-equal to the canonical definition.
- Public API (`/api/v1/ask`, `AskRequest`/`AskResponse`), CLI,
  `TextToSQLAgent` surface, `execute_query()`, session behavior,
  evaluation tooling, security and observability behavior: unchanged.
- Compatibility classes: source compatibility, runtime compatibility,
  API compatibility and behavioral compatibility are all preserved;
  verified by `test_backward_compatibility.py` + full suite (§16/§17).

## 12. Import Migration Strategy

Table in [`package-architecture.md` §7](../architecture/package-architecture.md#7-import-migration-strategy):
three migrations total (VO definition move + re-export, lazy
`__init__`, contracts import retarget). Nothing else moves.

## 13. Architecture Enforcement Plan

Six mechanisms in [`package-architecture.md` §10](../architecture/package-architecture.md#10-architecture-enforcement-plan-designed-in-3a-implemented-in-3b3c),
headlined by the **import-footprint test**: fresh-subprocess import of
`src.agents.contracts` / `src.agents.state` must not load `openai`,
`pymssql`, `fastapi`, `streamlit`, `langchain`, `requests`, `src.memory`,
or the façade. Plus per-layer forbidden-import extension of
`test_dependencies.py`, existing DFS cycle detection, canonical-ownership
identity tests, and import-time side-effect tests. Repository-native
pytest only — no new tooling dependency.

## 14. Files/Modules Proposed for Movement (3B)

1. `src/core/database/policy.py` — **new**; receives the *definitions* of
   `ExecutionPolicy`, `DatabaseError`, `DatabaseErrorCode`.
2. `src/core/database/connection.py` — imports those names from
   `policy.py` and re-exports them (definitions removed from this file).
3. `src/agents/__init__.py` — rewritten as lazy re-export surface.
4. `src/agents/contracts.py` — retarget its two imports to `policy.py`.
5. `tests/test_architecture/` — new footprint tests; extend
   `test_dependencies.py` rules.

## 15. Files/Modules Explicitly NOT to Move

`contracts.py` (kept whole), `state.py`, `orchestrator.py`,
`adapters.py`, `text_to_sql_agent.py`, `sql_security.py`,
`connection.py` (file stays), `sql_extractor/*`, `prompts.py`,
`column_mapper.py`, `column_mapper_updater.py`, `schema_hash.py`,
`memory/manager.py`, `api/*`, `web/app.py`, `utils/*`, `evaluation/*`,
`scripts/*`, `run*.py`.

## 16. Tests Executed

| Command | Scope |
|---|---|
| `uv run python -m pytest tests -q` | full suite |
| `uv run python -m pytest tests -q -rs` | full suite with skip reasons |
| `uv run python -m pytest tests/test_architecture tests/test_agents -q` | architecture + agent suites |
| `uv run python -m compileall -q src evaluation scripts tests` | compile validation |
| fresh-subprocess import-footprint probes (7 modules) | transitive import audit |

## 17. Test Results

- **Full suite: 668 passed, 340 skipped, 0 failed** (8.01 s).
- Architecture + agents subset: **567 passed, 340 skipped, 0 failed**.
- All 340 skips are by-design parametrization skips in
  `tests/test_architecture/test_validation.py`'s protocol-method matrix
  ("X is not part of Y") — none are environment failures.
- `compileall`: OK, no errors.
- No behavioral tests were added or removed in 3A (design-only stage).

## 18. Documentation/ADR Changes

- **Created** `docs/adr/ADR-016-package-architecture.md`.
- **Created** `docs/architecture/package-architecture.md`.
- **Created** `docs/sprints/sprint-2-prompt-3a-report.md` (this file).
- **Updated** `docs/architecture/dependency-rules.md` (companion-doc pointer only).

## 19. Risks

1. Lazy `__init__` changes import *timing*; any code relying on
   import-order side effects of `src.agents` would notice — none found
   (construction is instance-time, not import-time).
2. Moving the three VO definitions is a behavior-neutral refactor but
   touches the execution boundary's module — identity tests must prove
   the re-exports are the same objects, not copies.
3. The footprint test runs a subprocess per check — keep the checked
   surface small to avoid slowing the suite.
4. F3 (orchestrator → extractor → openai) is only fully resolved when
   `LLMRouter`/`SQLGenerator` land (Sprint 6); the footprint test will
   keep the contract/state surface clean meanwhile.

## 20. Recommended Implementation Sequence for Stage 3B

1. Add `src/core/database/policy.py` with the three definitions
   (verbatim move); `connection.py` imports + re-exports them.
2. Retarget `contracts.py` imports to `src.core.database.policy`.
3. Rewrite `src/agents/__init__.py` as PEP 562 lazy exports.
4. Add import-footprint tests (contracts/state isolation).
5. Extend `test_dependencies.py` layer rules to all `src` modules.
6. Fix F6 doc drift in `contracts.py` docstring.
7. Run full regression suite + compile validation; verify 668/340
   baseline unchanged.

## Explicit Statements

- **What was NOT changed**: no production source file was modified; only
  documentation was created/updated.
- **Runtime behavior changed**: NO.
- **Public API compatibility changed**: NO.
- **Security behavior changed**: NO.
- **Database execution behavior changed**: NO.

**STOP — Stage 3A complete. Stage 3B not started.**
