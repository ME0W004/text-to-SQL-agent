# ADR-016: Package Architecture & Enforceable Dependency Boundaries

**Status**: Accepted; implemented in Sprint 2 / Prompt 3B
**Supersedes**: none
**Companion documents**:
- [`docs/architecture/package-architecture.md`](../architecture/package-architecture.md)
- [`docs/architecture/dependency-rules.md`](dependency-rules.md)
- [`docs/sprints/sprint-2-prompt-3a-report.md`](../sprints/sprint-2-prompt-3a-report.md)

## Context

Sprint 2 Prompts 1–2 introduced `AgentState`, typed contracts, the
Orchestrator, and static architecture tests. The architecture principles
(state single-writer, DB boundary, LLM boundary, contract authority) are
documented and partially enforced, but the **package structure itself**
still lets boundary violations in through two confirmed doors:

1. **Eager package `__init__`** — `src/agents/__init__.py` imports the
   façade (`TextToSQLAgent`), the Orchestrator and the adapters at
   package-import time. Verified footprint: importing *any*
   `src.agents.*` module — including the pure contract module
   `src.agents.contracts` — transitively imports 29 `src.*` modules plus
   the **openai SDK**, **pymssql**, `requests`, `dotenv` and `pydantic`.
   The contract module is therefore not a lightweight, dependency-free
   surface. Existing architecture tests only check *direct* per-file
   imports, so this leak is invisible to them.

2. **Value objects co-located with the DB driver adapter** —
   `src/core/database/connection.py` imports `pymssql` at module top and
   also defines `ExecutionPolicy`, `DatabaseError`, `DatabaseErrorCode`.
   `src/agents/contracts.py` imports those two names from it, so the
   contract authority necessarily pulls the DB driver even after the
   `__init__` problem is fixed.

3. **Legacy validator wrapper** — `src/core/sql_extractor/validator.py`
   defines its own `SQLValidationError`/`SQLValidator` over the Security
   Core. Documented migration debt per `dependency-rules.md` §11 and
   ADR-014; not a package-boundary defect.

The live internal import graph is acyclic (verified by
`test_no_dependency_cycles_among_internal_packages`); API/UI layers
depend only on the façade; `src.core.security.sql_security` is pure
(`sqlglot` only); `src.memory.manager` is self-contained (`sqlite3`).

## Decision

1. **Contracts authority: keep `src/agents/contracts.py` as the single
   canonical contract module (Option A).** Do not split it into a
   package. Evidence: it is imported by state, orchestrator, adapters
   and ~500 test call sites; its internal cohesion is high; splitting
   adds compatibility-shim cost with no boundary gain. The *only*
   sanctioned change is fixing its import direction (item 3).

2. **Make `src/agents/__init__.py` lazy** (PEP 562 module
   `__getattr__`). Public re-exports (`TextToSQLAgent`, `Orchestrator`,
   adapters, state symbols) must keep working; importing
   `src.agents.contracts` or `src.agents.state` must no longer import
   the façade, provider SDKs, or the DB driver.

3. **Move the driver-free value objects** `ExecutionPolicy`,
   `DatabaseError`, `DatabaseErrorCode` to a new pure module
   `src/core/database/policy.py`. `connection.py` re-exports them
   (compatibility); `contracts.py` imports them from the pure module.
   The DB boundary itself (who may talk to SQL Server) is unchanged.

4. **No artificial layering.** The existing top-level packages
   (`agents`, `core`, `memory`, `api`, `web`, `utils`) are retained;
   target layers are documented conceptually
   (`package-architecture.md`) and enforced by import rules, not by
   moving every file.

5. **Enforcement becomes transitive.** Architecture tests are extended
   from direct-import AST checks to package-level footprint checks:
   importing `src.agents.contracts` / `src.agents.state` in a fresh
   interpreter must not load `openai`, `pymssql`, `fastapi`,
   `streamlit`, or `src.memory`.

## Consequences

- Public API, CLI, evaluation, security and execution behavior are
  unchanged; this is an import-structure and enforcement refactor.
- `connection.py` keeps its names via re-export; the canonical import
  path for the three value objects becomes `src.core.database.policy`.
- New architecture tests make the documented rules executable;
  documentation that claims a boundary must point at a test.

## Alternatives considered

- **Split `contracts.py` into a package** — rejected: high shim cost,
  no boundary gain, contradicts "no unnecessary package fragmentation".
- **Introduce an import-linter / external dependency** — rejected:
  repository-native pytest AST checks already exist; a third-party
  checker is not justified (per task global requirements).
- **Move provider clients behind a lazy factory only** — insufficient:
  fixes symptom (SDK import) but leaves contracts → driver coupling.
