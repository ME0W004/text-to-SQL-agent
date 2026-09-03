# Sprint 2 / Prompt 3B Report — Package Restructuring & Dependency Refactor

**Date**: 2026-09-03
**Stage**: 3B (implementation of the approved 3A design)
**Decision record**: [`ADR-016`](../adr/ADR-016-package-architecture.md)
**Design document**: [`package-architecture.md`](../architecture/package-architecture.md)
**Baseline report**: [`sprint-2-prompt-3a-report.md`](sprint-2-prompt-3a-report.md)

## 1. Executive Summary

The approved Stage 3A design was implemented exactly. The three value
objects (`ExecutionPolicy`, `DatabaseError`, `DatabaseErrorCode`) now
live canonically in the driver-free `src/core/database/policy.py`;
`connection.py` re-exports the same objects (identity-verified);
`contracts.py` imports them from `policy.py` and no longer touches the
pymssql adapter module; `src/agents/__init__.py` and
`src/core/database/__init__.py` are lazy PEP-562 export surfaces.
Importing `src.agents.contracts` or `src.agents.state` now loads only 10
pure `src` modules and **zero** heavy dependencies (previously: 29 `src`
modules + openai + pymssql + requests + dotenv + pydantic). New
architecture tests enforce the footprint, canonical ownership and layer
rules. Full regression: **688 passed, 340 skipped, 0 failed** — the 668
baseline tests all still pass; 20 new architecture tests were added.

## 2. Baseline Before Changes

Recorded immediately before any change (matches the approved 3A report
exactly — no discrepancy):

| Check | Result |
|---|---|
| `uv run python -m pytest tests -q` | **668 passed, 340 skipped, 0 failed** (7.61 s) |
| `uv run python -m pytest tests/test_architecture tests/test_agents -q` | **567 passed, 340 skipped, 0 failed** |
| `uv run python -m compileall -q src evaluation scripts tests` | OK, no errors |

## 3. Implemented Package Changes

| Path | Change | Reason | 3A decision |
|---|---|---|---|
| `src/core/database/policy.py` | **New.** Canonical definitions of `ExecutionPolicy`, `DatabaseError`, `DatabaseErrorCode`, moved verbatim (docstrings, defaults, validation, `from_environment` preserved). Imports: `os`, `dataclasses`, `enum`, `src.utils.exceptions` only. | F2 — contract authority must not transitively import pymssql | Design §7 migration table; ADR-016 decision 3 |
| `src/core/database/connection.py` | Definitions removed; imports the three names from `policy.py` and re-exports them (explicit `__all__`). Removed now-unused `os`/`dataclass`/`Enum`/`QueryExecutionError` imports. All execution logic untouched. | F2 — compatibility re-export surface | ADR-016 decisions 3+5 |
| `src/core/database/__init__.py` | Rewritten as lazy PEP-562 export surface (same four public names). | Required: the eager `from .connection import ...` would still load pymssql whenever `policy` was imported through the package | ADR-016 decisions 2+3 (same mechanism, applied to the second package `__init__` on the lightweight path) |
| `src/agents/contracts.py` | Import of `ExecutionPolicy`/`DatabaseErrorCode` retargeted from `connection` to `policy`; docstring updated (dependency description + F6 test-reference drift fixed). No contract type changed. | F2 + F6 | ADR-016 decisions 3+6; 3A design item 10 |
| `src/agents/__init__.py` | Rewritten as explicit lazy PEP-562 export surface (19 public names, hand-maintained mapping, `__dir__`, documented rationale). | F1 — importing any `src.agents.*` module pulled the whole stack incl. openai SDK | ADR-016 decision 2 |
| `tests/test_architecture/test_import_footprint.py` | **New.** Subprocess footprint tests, lazy-export identity tests, canonical-ownership tests, policy purity tests. | Enforcement | 3A design §10 items 1+4 |
| `tests/test_architecture/test_dependencies.py` | Added `test_does_not_import_database_driver_module` + `test_imports_policy_module` to the contracts class; new `TestLightweightAgentModules` (state.py, policy.py forbidden imports). | Enforcement | 3A design §10 item 2 |

## 4. `policy.py` Canonical Ownership

- **Definitions moved** (verbatim): `DatabaseErrorCode` (8 members,
  values unchanged), `DatabaseError` (constructor signature
  `(code, message, *, sql=None, cause=None)` unchanged, still subclasses
  `QueryExecutionError`), `ExecutionPolicy` (frozen dataclass, defaults
  `1000/30/5/100`, `__post_init__` validation, `from_environment()`
  env-var names unchanged).
- **Imports**: `__future__`, `os`, `dataclasses`, `enum`,
  `src.utils.exceptions` — asserted minimal by
  `TestPolicyModulePurity::test_policy_imports_are_minimal`.
- **Driver-free verification**: AST forbidden-import test +
  subprocess footprint probe (importing `src.agents.contracts` does not
  load `pymssql` or even `src.core.database.connection`).
- **Compatibility re-exports**: `connection.py` re-exports all three
  names under an explicit `__all__`; `src/core/database/__init__.py`
  re-exports the same four names lazily.
- **Identity verification** (runtime, in
  `TestCanonicalOwnership`): `policy.ExecutionPolicy is
  connection.ExecutionPolicy`, same for `DatabaseError` and
  `DatabaseErrorCode`; `contracts.ExecutionPolicy is
  policy.ExecutionPolicy`. Static guard:
  `test_definitions_live_only_in_policy` rejects any re-emergent
  `class` definition of the three names in connection/`__init__`/contracts.

## 5. `src.agents` Lazy Export Design

- **Mechanism**: PEP 562 module-level `__getattr__` with an explicit
  hand-maintained `_EXPORTS` mapping (name → defining module); `import
  importlib` is deferred inside `__getattr__`; `__dir__` merges
  globals + exports. No wildcard imports, no scanning, no side effects.
- **Publicly importable**: exactly the 19 names previously exported
  (`TextToSQLAgent`, 15 state symbols, `Orchestrator`,
  `OrchestratorDeps`, `SecurityValidatorAdapter`,
  `QueryExecutorAdapter`) — same `__all__` list as before.
- **Heavy imports removed from import time**: façade, Orchestrator,
  adapters (and therefore openai, pymssql, requests, dotenv, pydantic,
  `src.memory`). Verified by `TestImportFootprint`.
- **Same pattern applied to `src/core/database/__init__.py`** so
  `from src.core.database import DatabaseConnection` still works while
  `src.core.database.policy` imports stay driver-free.
- **Compatibility**: all 19 lazy exports identity-checked against their
  defining modules by `TestLazyPackageExports`; `import run` (CLI) and
  `src.api.main` verified.

## 6. `contracts.py` Dependency Changes

- **Old**: `from src.core.database.connection import DatabaseErrorCode, ExecutionPolicy`
  — pulled `pymssql` transitively.
- **New**: `from src.core.database.policy import DatabaseErrorCode, ExecutionPolicy`
  — driver-free.
- **Why correct**: the two names are pure value objects; their
  definition module must not own the driver. `policy.py` sits beside
  `connection.py` inside the database boundary package, so the DB
  boundary itself (only `QueryExecutor`/`DatabaseConnection` crosses to
  SQL Server) is unchanged.
- **Single authority preserved**: `contracts.py` remains the one
  contract module; it re-exports the canonical objects (identity
  tested). No type was split, duplicated or renamed.

## 7. Architecture Tests

**Added — `tests/test_architecture/test_import_footprint.py`** (subprocess isolation used for footprint tests):

| Test | Protects |
|---|---|
| `TestImportFootprint::test_lightweight_module_does_not_load_heavy_stack` (×2) | contracts/state must not load openai, pymssql, fastapi, streamlit, langchain*, requests, dotenv, pydantic, `src.memory.manager`, façade (F1/F2/F3 regression) |
| `TestImportFootprint::test_lightweight_module_does_not_load_the_facade` (×2) | contracts/state must not import façade/orchestrator/adapters modules |
| `TestImportFootprint::test_contracts_does_not_load_database_driver_module` | contracts must not even load `connection.py` (F2) |
| `TestLazyPackageExports::*` (4) | lazy `__getattr__` surfaces resolve to the defining modules' exact objects; `dir()` completeness; unknown attrs raise |
| `TestCanonicalOwnership::test_policy_and_connection_names_are_identical` | re-export identity (not copies) |
| `TestCanonicalOwnership::test_contracts_uses_canonical_policy_objects` | contracts uses canonical objects |
| `TestCanonicalOwnership::test_definitions_live_only_in_policy` | no duplicate definitions in connection/`__init__`/contracts |
| `TestPolicyModulePurity::*` (2) | policy.py stays driver-free with a minimal, allow-listed import set |

**Modified — `tests/test_architecture/test_dependencies.py`**:

| Test | Protects |
|---|---|
| `TestContractsModuleBoundaries::test_does_not_import_database_driver_module` | contracts must not re-import `connection.py` |
| `TestContractsModuleBoundaries::test_imports_policy_module` | canonical path is actually used |
| `TestLightweightAgentModules` (8 parametrized cases) | state.py and policy.py import no drivers/SDKs/sqlite3/memory/API-UI |

Existing cycle detection (`test_no_dependency_cycles_among_internal_packages`)
and single-writer tests are unchanged and pass.

## 8. Dependency Boundary Validation

- **Driver leakage**: eliminated on the lightweight surface — importing
  `src.agents.contracts`/`state` loads no `pymssql`, no
  `connection.py` (probe-verified + tests). The adapter layer
  (`adapters.py`, `orchestrator.py`) is still the sanctioned pymssql
  touchpoint, unchanged.
- **Provider leakage**: contracts/state load no openai/langchain SDKs
  (probe-verified). F3 (orchestrator chain → openai) remains documented
  migration debt deferred to LLMRouter/SQLGenerator work — the
  lightweight surface itself is now clean.
- **API/UI leakage**: contracts/state/policy import no fastapi/streamlit
  (tests). `routes.py` still imports only the façade (existing test).
- **Memory leakage**: `src.memory.manager` not loaded by any lightweight
  module (probe + AST tests).
- **Cycles**: none — existing DFS cycle test passes on the live graph.
- **Canonical ownership**: enforced by identity + AST tests (§4/§7).

## 9. Compatibility Validation

- **Source compatibility**: every old import path works —
  `from src.core.database.connection import ExecutionPolicy|DatabaseError|DatabaseErrorCode`,
  `from src.core.database import DatabaseConnection|...`,
  `from src.agents import TextToSQLAgent` and all 18 other package
  exports, `from src.agents.contracts import ExecutionPolicy`. Verified
  at runtime and by tests.
- **Runtime compatibility**: identity guarantees mean every consumer
  receives the *same objects* (same enum members, same exception
  hierarchy, same dataclass). `isinstance`/`except` behavior identical.
- **API compatibility**: `src.api.main` imports cleanly; full API test
  suite passes; `AskRequest`/`AskResponse` untouched.
- **Behavioral compatibility**: full regression suite green (§10);
  security, execution, observability and evaluation suites all pass.

## 10. Regression Validation

| Suite | Command | Result |
|---|---|---|
| Full suite | `uv run python -m pytest tests -q` | **688 passed, 340 skipped, 0 failed** (10.43 s) |
| Skip audit | `uv run python -m pytest tests -q -rs` | all 340 skips are the by-design protocol-matrix skips in `test_validation.py` |
| Architecture + agents | `pytest tests/test_architecture tests/test_agents -q` | **587 passed, 340 skipped, 0 failed** |
| Security / DB / observability / evaluation / integration | `pytest tests/test_security tests/test_database tests/test_observability tests/test_evaluation tests/test_integration -q` | **101 passed, 0 failed** |
| Compile/import | `uv run python -m compileall -q src evaluation scripts tests` | OK |
| CLI import | `import run; callable(run.main)` | OK |
| Formatter/static | none configured in repo (no ruff/black/flake8 config present) | n/a |

Delta vs baseline: **+20 passed** — exactly the 20 new architecture
tests; all 668 baseline tests unchanged and green; skips unchanged at 340.

## 11. Security Validation

Security behavior changed: **NO**. `SQLSecurityValidator`, its inline
invocation inside `execute_query`, fail-closed handling, and all
security tests (`tests/test_security`, 101-test targeted run) are
untouched. `sql_security.py` was not modified.

## 12. Database Execution Validation

Execution semantics changed: **NO**. The `DatabaseConnection` body is
byte-for-byte unchanged except the module's import header; timeouts,
row caps, fetch batching, cursor lifecycle, error mapping and
`ExecutionPolicy` defaults/validation are identical (definitions moved
verbatim). `tests/test_database` and `test_safe_execution` pass.

## 13. State & Concurrency Validation

AgentState ownership/isolation changed: **NO**. `state.py` untouched;
single-writer tests, state isolation and applier tests all pass;
Orchestrator untouched.

## 14. Observability Validation

Tracing/logging/sanitization changed: **NO**. `utils/logger.py`
untouched; `tests/test_observability` pass; `log_event`/`sql_fingerprint`
call sites unchanged.

## 15. Files Changed

Production:
- `src/core/database/policy.py` (new)
- `src/core/database/connection.py`
- `src/core/database/__init__.py`
- `src/agents/contracts.py`
- `src/agents/__init__.py`

Tests:
- `tests/test_architecture/test_import_footprint.py` (new)
- `tests/test_architecture/test_dependencies.py`

Docs:
- `docs/adr/ADR-016-package-architecture.md` (status line)
- `docs/sprints/sprint-2-prompt-3b-report.md` (new)

## 16. Remaining Migration Debt

- **Pre-existing**: F3 orchestrator→extractor→openai transitive SDK
  import (until LLMRouter/SQLGenerator, Sprint 6); legacy
  `SQLValidator` wrapper (ADR-014); `os.getenv` in legacy components
  (grandfathered); `LLMClient` before `LLMRouter`.
- **Introduced by this refactor**: permanent compatibility re-exports of
  the three value objects from `connection.py` (identity-tested, by
  design; retire with the 3C canonical-ownership tests in place).
- **Intentionally deferred**: `src/core/__init__.py` eagerly loads the
  pure `column_mapper` data module into the contracts footprint (10 src
  modules loaded, all dependency-free) — harmless today; could be made
  lazy if the footprint ever grows.

## 17. Deviations From Stage 3A

**One minor addition**: Stage 3A's file list did not name
`src/core/database/__init__.py`. Making it lazy was **required** to
fulfill the approved design — the eager `from .connection import ...`
in that file would have re-introduced the pymssql import every time
`policy` was reached through the package. Same mechanism (PEP 562), same
public names, no structure change. Everything else follows the 3A design
exactly.

## 18. Risks

1. Lazy `__getattr__` changes error semantics for typos on the package
   object: an unknown attribute now raises `AttributeError` from
   `__getattr__` (tested). Pickling of package attributes is unaffected
   in practice (all exports are classes/functions).
2. Any future code doing `from src.core.database import *` relies on
   `__all__` — identical to before, but new exports must be added to
   `_EXPORTS` in two package inits (documented in-module).
3. The footprint tests spawn short-lived subprocesses (~1 s each,
   5 total); suite time grew by ~2 s.

## 19. Final Status

**PASS**

- Runtime behavior changed: **NO**
- Public API compatibility changed: **NO**
- Security behavior changed: **NO**
- Database execution behavior changed: **NO**
- AgentState ownership changed: **NO**
- Observability behavior changed: **NO**

## 20. Recommendation

**Stage 3C is ready to begin** — the package architecture matches the
approved design, all boundaries are implemented and test-enforced, and
the full regression suite is green. 3C should focus on deepening
enforcement (import-time side-effect tests, singleton/global-state
checks, developer architecture guide) as scoped in the 3A enforcement
plan.
