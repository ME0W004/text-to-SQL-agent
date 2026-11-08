# Package Architecture — Target Design & Dependency Audit

**Sprint**: 2 — Prompt 3A (audit & design; no production refactor performed)
**Status**: Design approved; implementation in Prompt 3B
**Decision record**: [`ADR-016`](../adr/ADR-016-package-architecture.md)
**Companion documents**:
- [`dependency-rules.md`](dependency-rules.md)
- [`component-responsibilities.md`](component-responsibilities.md)

---

## 1. Current Package/Module Inventory

| Module | Classification | Notes |
|---|---|---|
| `src/agents/__init__.py` | **Package root (defect)** | Eagerly imports façade + Orchestrator + adapters; causes whole-stack import (see §3, F1) |
| `src/agents/contracts.py` | **Contracts (canonical)** | Typed VOs, Protocols, typed errors; imports security core (pure) + `connection.py` (pulls pymssql) — see §3, F2 |
| `src/agents/state.py` | Application/state | AgentState; imports contracts + security + logger. Orchestrator-side only |
| `src/agents/orchestrator.py` | Application/orchestration | Single state writer (ADR-013) |
| `src/agents/adapters.py` | Adapters | Wraps `SQLSecurityValidator`, `DatabaseConnection` into contracts; allowed infra touchpoint |
| `src/agents/text_to_sql_agent.py` | Interface/composition root | Public façade; constructs infra; preserved API |
| `src/core/security/sql_security.py` | **Security domain (pure)** | `sqlglot` only. Authoritative security core |
| `src/core/database/connection.py` | DB adapter (infrastructure) | `pymssql` at top; also defines `ExecutionPolicy`/`DatabaseError`/`DatabaseErrorCode` (VOs) — see §3, F2 |
| `src/core/database/policy.py` | Execution policy VOs (pure) | Driver-free leaf (ADR-016, Sprint 2/3B); canonical home of the three VOs |
| `src/core/database/metadata.py` | **Schema metadata adapter (infrastructure, Sprint 3.1B)** | Second (lazy) approved `pymssql` import site; read-only introspection; driver-free typed output (ADR-017) |
| `src/core/schema/catalog.py` | **Schema Intelligence catalog (infrastructure, Sprint 3.1B)** | `SchemaCatalog` per ADR-002/017; owns annotations/merge/fingerprint/cache/history; no driver, LLM, state imports (enforced) |
| `src/core/database/models.py` | Data (schema constants) | `FULL_SCHEMA`; consumed by `prompts.py` |
| `src/core/llm/base.py`, `client.py` | LLM boundary | `client.py` imports `requests` + `OpenAIClient` eagerly |
| `src/core/llm/{openai,gemini,groq,ollama}_client.py` | Provider adapters | Each imports its SDK at module top (by design, behind LLM boundary) |
| `src/core/prompts.py` | Prompt templates | Coupled to `database.models.FULL_SCHEMA` (data dependency, acceptable) |
| `src/core/sql_extractor/` | Legacy generation component | `extractor.py` consumes `LLMClient` (LLM boundary OK); `validator.py` wraps Security Core (migration debt, ADR-014) |
| `src/core/column_mapper.py` | Generated data | Auto-generated; imported with `try/except` in façade |
| `src/core/column_mapper_updater.py` | Application maintenance | Uses `subprocess`, cache file, `DatabaseConnection`; acceptable as maintenance utility |
| `src/core/schema_hash.py` | DB introspection utility | Reads `INFORMATION_SCHEMA` via `DatabaseConnection` — DB-touching, must stay out of domain |
| `src/memory/manager.py` | Memory adapter (self-contained) | `sqlite3` + `os` only |
| `src/api/{main,routes,models}.py` | Interface (FastAPI) | routes.py imports only the façade — boundary holds |
| `src/web/app.py` | Interface (Streamlit) | Talks to API over HTTP; fully isolated |
| `src/utils/{logger,config_loader,exceptions}.py` | Cross-cutting | Shared utility surface per `dependency-rules.md` §7 |
| `evaluation/` | Test-only harness | Imports the agent lazily inside `run.py`; models/metrics/runner pure |
| `scripts/generate_column_map.py` | Maintenance script | Direct infra imports; script-level, acceptable |
| `run.py`, `run_api.py`, `run_ui.py` | Entrypoints | CLI / uvicorn / streamlit launchers |

## 2. Current Dependency Graph (verified edges)

```
api/web (interface)  ──►  text_to_sql_agent (façade)  ──►  orchestrator ──► contracts
                                   │                        │    │            ▲
                                   │                        │    └──► adapters┘
                                   │                        │           │   ▲
                                   │                        ▼           ▼   │
                                   ├──► sql_extractor ──► llm.client ──► openai SDK
                                   ├──► memory.manager     │  (requests)
                                   ├──► database.connection ──► pymssql ──► security (sqlglot)
                                   └──► prompts ──► database.models (data)
state ──► contracts, security, logger
evaluation ──► (lazy) façade only
```

Import-direction checks performed:
- No `src/core|memory|utils` module imports `src.agents` — **no upward dependencies**.
- No module imports `src.api` / `src.web` from within `src` production code.
- Live graph is acyclic (existing DFS cycle test passes).

## 3. Confirmed Findings

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| F1 | **Critical** | `src/agents/__init__.py` eagerly imports façade/Orchestrator/adapters → importing *any* `src.agents.*` module (incl. `contracts`, `state`) loads 29 `src.*` modules + **openai SDK** + **pymssql** + `requests` + `dotenv` + `pydantic` + `sqlite3` | Fresh-subprocess footprint probe (Prompt 3A); existing tests miss it because they check direct imports only |
| F2 | **High** | `contracts.py` imports `ExecutionPolicy`/`DatabaseErrorCode` from `connection.py`, which imports `pymssql` at module top → the contract authority can never be driver-free | `src/agents/contracts.py:43-47`, `src/core/database/connection.py:18` |
| F3 | **High** | Orchestrator chain transitively imports the openai SDK (`orchestrator → sql_extractor.extractor → llm.client → openai_client`) — spirit of the "no provider SDK above the LLM boundary" rule | footprint probe; direct-import test passes — **RESOLVED in Sprint 3 / Prompt 3.0B**: the extractor imports no LLM module; the openai SDK loads only on provider construction (pinned by `tests/test_llm/test_import_purity.py` façade/Orchestrator probes) |
| F4 | Medium | `sql_extractor/validator.py` defines a parallel `SQLValidator`/`SQLValidationError` over the Security Core, with a class-level mutable singleton | migration debt (ADR-014, `dependency-rules.md` §11) |
| F5 | Medium | Name shadowing: `contracts.SyntaxError` and `contracts.MemoryError` shadow builtins inside the module namespace | cosmetic; no runtime defect found |
| F6 | Low | `contracts.py` docstring references `tests/test_architecture/test_contracts_dependencies.py`, which does not exist (test lives in `test_dependencies.py`) | doc drift |
| F7 | Low | `SecurityValidatorAdapter.validate` constructs a throwaway `SQLSecurityValidator` per call when a non-default policy is passed | acceptable; behavior-preserving |
| A1 | Acceptable | Security Core invoked *inline* inside `DatabaseConnection.execute_query` | documented integration (`dependency-rules.md` §3.10) |
| A2 | Acceptable | Façade constructs infrastructure directly (composition root role) | by design since Prompt 2B |
| A3 | Acceptable | `MemoryManager` path defaults to repo-relative `data/conversations.db` | unchanged Sprint 1 behavior |
| D1 | Migration debt | `LLMClient` is OpenAI-flavored; `LLMRouter` per contracts is the future boundary | Sprint 6 (existing plan) |
| D2 | Migration debt | `os.getenv` direct config access in legacy components | `dependency-rules.md` §4.6 grandfather clause |

**No confirmed dependency cycles. No state leakage (API never touches
AgentState). No DB/provider leakage outside the documented adapters —
except through the F1/F2 transitive-import doors (F3 closed in Sprint 3;
see the findings table above).**

## 4. Target Package Tree

Structurally identical to today except for one new module; the change is
in **import direction and import-time weight**, not in file layout:

```
src/
  agents/                          # application layer
    __init__.py                    #   LAZY re-exports (PEP 562)          [3B change]
    contracts.py                   #   canonical contracts (single file)
    state.py                       #   AgentState (Orchestrator-only writes)
    orchestrator.py                #   application orchestration
    adapters.py                    #   adapters to existing infra
    text_to_sql_agent.py           #   public façade / composition root
  core/
    security/sql_security.py       # security domain (pure, sqlglot)
    database/
      policy.py                    # ExecutionPolicy/DatabaseError/DatabaseErrorCode  [3B: NEW, pure]
      connection.py                # pymssql adapter (re-exports policy VOs)          [3B: re-export]
      metadata.py                  # schema metadata adapter (lazy pymssql)           [3.1B: NEW, ADR-017]
      models.py                    # schema data (legacy, deprecated — see ADR-017)
    llm/                           # LLM boundary (client.py) + provider adapters
    schema/
      catalog.py                   # SchemaCatalog (annotations/fingerprint/cache)    [3.1B: NEW, ADR-017]
      retriever.py                 # SchemaRetriever (bounded deterministic selection) [3.1C: NEW, hardened 3.2, ADR-017]
      renderer.py                  # SchemaContextRenderer (bounded LLM-facing render)  [3.2: NEW, dormant]
    sql_extractor/                 # legacy generation component (untouched in 3B)
    prompts.py, column_mapper.py, column_mapper_updater.py, schema_hash.py  # legacy schema surfaces (deprecated, untouched)
  memory/manager.py                # memory adapter
  api/                             # FastAPI interface
  web/                             # Streamlit interface
  utils/                           # cross-cutting: logger, config, exceptions
evaluation/                        # test-only harness (no production imports)
scripts/, run.py, run_api.py, run_ui.py
```

## 5. Dependency Direction Matrix

| SOURCE layer | Allowed dependencies | Forbidden dependencies |
|---|---|---|
| **contracts** | stdlib, `src.core.security.sql_security`, `src.core.database.policy`, `src.utils.logger` | `src.agents.state`, `src.agents.adapters`, `src.api.*`, `src.web.*`, `pymssql`, `sqlite3`, provider SDKs, `fastapi`, `streamlit`, `evaluation.*` |
| **state** (orchestrator-side) | contracts, security core, logger | adapters, façade, api/web, infra drivers |
| **application/orchestration** (orchestrator) | contracts, state, adapters, security core, logger, exceptions | direct `pymssql`, provider SDKs, `fastapi`, `streamlit`, MemoryManager impl (via `MemoryManagerProtocol` only) |
| **adapters** | contracts, security core, `database.connection`, logger | state, api/web |
| **security domain** | sqlglot, stdlib | everything in `src` except `src.utils.logger` |
| **execution (DB adapter)** | `pymssql`, security core, `policy.py`, logger, exceptions | agents (any), api/web, memory |
| **LLM boundary + provider adapters** | provider SDKs, `requests`, logger | agents (any), database, memory, api/web |
| **memory adapter** | `sqlite3`, stdlib | everything in `src` except logger |
| **interface (api/web)** | façade (`TextToSQLAgent`), api models, logger | orchestrator/contracts/adapters/state internals, `pymssql`, provider SDKs |
| **façade / composition root** | everything (wiring role) | — (may touch all, but must not be imported by contracts/state/application) |
| **evaluation** | façade (lazy), own models | — (test-only; nothing in `src` may import it) |

Logging (`src.utils.logger`) is the shared utility surface for all rows
(`dependency-rules.md` §7) and is not counted as a layer violation.

## 6. Ownership Rules

| Responsibility | Lives in | May import it | Protected by |
|---|---|---|---|
| Contract authority (VOs/Protocols/typed errors) | `src/agents/contracts.py` | everyone below the façade | contract-import-footprint test (no driver/SDK/state) |
| AgentState + appliers | `src/agents/state.py` | orchestrator, façade only | single-writer tests (ADR-013) |
| State writing | `orchestrator.run` | — | `test_single_writer*`, AST `with_*` scan |
| SQL security decisions | `core/security/sql_security.py` | all (pure) | ADR-004/014; security tests |
| SQL Server access | `core/database/connection.py` | adapters, façade, schema utilities, scripts | DB-boundary tests; only `QueryExecutor` contract crosses the boundary |
| Execution policy VOs (3B) | `core/database/policy.py` | contracts, adapters, connection | import-footprint test |
| LLM provider access | `core/llm/*` | `client.py`, extractor | provider-isolation tests |
| Conversation memory | `memory/manager.py` | façade, orchestrator (via protocol) | MemoryManager not imported by contracts/orchestrator |
| HTTP interface | `api/` | callers/entrypoints | API-must-not-import-orchestrator test |
| Compatibility shims | re-exports in `connection.py`, lazy `agents/__init__` | legacy consumers | shim identity tests (same object, not a copy) |

**Compatibility rule**: old import paths keep working via re-export; a
shim must resolve to the *same* object (`is` identity), never a
duplicate definition.

## 7. Import Migration Strategy (3B)

| Current path | Target path | Shim | Consumers | Lifetime |
|---|---|---|---|---|
| `src.core.database.connection::{ExecutionPolicy,DatabaseError,DatabaseErrorCode}` (definition) | `src.core.database.policy` (definition) | `connection.py` re-exports names | contracts, adapters, orchestrator, tests | old path: permanent re-export; definition: canonical in `policy.py` |
| `src.agents.__init__` eager imports | PEP 562 lazy `__getattr__` | none needed — `from src.agents import TextToSQLAgent` unchanged | api, run.py, evaluation, tests | permanent |
| `src.agents.contracts` import of connection | import from `policy` | none (internal) | — | — |

Nothing else moves in 3B. `sql_extractor`, `prompts`, `column_mapper*`,
`schema_hash`, `memory`, `api`, `web` stay put (see §9).

## 8. Compatibility Constraints (unchanged by design)

`/api/v1/ask`, `AskRequest`/`AskResponse`, CLI behavior,
`TextToSQLAgent()` public surface, `execute_query()` compatibility
surface, session behavior, evaluation tooling, security behavior,
observability behavior — all must be preserved; covered by
`tests/test_architecture/test_backward_compatibility.py` and the full
regression suite (baseline: **668 passed / 340 skipped**, skips are
by-design protocol-matrix parametrization skips in
`test_validation.py`).

## 9. Explicitly NOT Moved

`contracts.py` (kept whole), `state.py`, `orchestrator.py`,
`adapters.py`, `text_to_sql_agent.py`, `sql_security.py`,
`connection.py` (file stays; only three VO definitions relocate),
`sql_extractor/*`, `prompts.py`, `column_mapper*.py`, `schema_hash.py`,
`memory/manager.py`, `api/*`, `web/app.py`, `utils/*`, `evaluation/*`,
`scripts/*`, entrypoints.

## 10. Architecture Enforcement Plan (designed in 3A; implemented in 3B/3C)

> **Status (Stage 3C)**: items 1, 2, 4, 5 and 6 are implemented —
> footprint probes cover `contracts`, `state`, `policy` and the
> `src.core.database` package; the layer rules run as whole-tree scans
> (`test_boundary_hardening.py`); canonical ownership is scanned across
> all of `src/`; import-time side effects are probed with CPython audit
> hooks. For the practical developer summary see
> [`developer-package-guide.md`](developer-package-guide.md).

1. **Import-footprint tests** (new): in a fresh subprocess, import
   `src.agents.contracts` and `src.agents.state`; assert `openai`,
   `pymssql`, `fastapi`, `streamlit`, `langchain`, `requests`,
   `src.memory`, `src.agents.text_to_sql_agent` are **not** in
   `sys.modules`. Kills F1/F2/F3 regressions permanently.
2. **Layer forbidden-import tests** (extend `test_dependencies.py`):
   apply the §5 matrix to *all* `src` modules (per-layer rule table),
   not just the three Prompt-2B modules.
3. **Cycle detection**: already present
   (`test_no_dependency_cycles_among_internal_packages`); keep, no
   whitelist.
4. **Canonical-ownership tests** (3C): `policy.py` is the *only* module
   defining the three VOs; `connection.py` re-export is identity-checked
   (`DatabaseConnection` names are the same object both ways).
5. **Import-time side-effect tests** (3C): importing application modules
   opens no DB connections, writes no cache files, creates no sqlite
   files, and calls no network APIs.
6. No third-party enforcement tooling; repository-native pytest only.
