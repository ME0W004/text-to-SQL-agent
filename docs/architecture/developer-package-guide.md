# Developer Package Guide — Where Code Belongs & How Boundaries Are Enforced

**Audience**: developers implementing the next Text-to-SQL components
(Sprint 3: LLMRouter, SQLGenerator, Schema Intelligence, Query Planning,
Semantic Validation, ...).

**Authority**: this guide is a practical summary. The authoritative
documents are
[`ADR-016`](../adr/ADR-016-package-architecture.md) and
[`package-architecture.md`](package-architecture.md);
[`dependency-rules.md`](dependency-rules.md) defines the per-component
rules. Where they disagree, the ADR wins. Every rule below is backed by
a test — the test is the enforcement, not this document.

## 1. Where new code belongs

| You are writing... | Put it in | May import |
|---|---|---|
| Typed value objects, Protocols, error codes | `src/agents/contracts.py` | stdlib, `src/core/security/sql_security`, `src/core/database/policy`, `src/utils/logger` |
| Agent state model (read/write model, appliers) | `src/agents/state.py` | contracts, security, `src/utils/logger` |
| A pipeline component (analysis, planning, generation, validation) | its own module under `src/core/<subsystem>/` | contracts, `src/utils/*`; **not** AgentState |
| The component that advances AgentState | `src/agents/orchestrator.py` | everything above, adapters, state |
| A wrapper around existing infra (DB, security) | `src/agents/adapters.py` | `src/core/database/connection`, `src/core/security` |
| A provider client (OpenAI/Gemini/Groq/Ollama) | `src/core/llm/` | its SDK, `src/utils/*` |
| API routes / models | `src/api/` | the façade (`src.agents.text_to_sql_agent`) only |
| UI | `src/web/` | the façade only |
| Memory (session persistence) | `src/memory/` | stdlib (`sqlite3`), `src/utils/*` |
| DB driver adapter | `src/core/database/connection.py` | `pymssql`, `policy.py` |
| Driver-free DB value objects | `src/core/database/policy.py` | stdlib, `src/utils/exceptions` |

Shared value objects (`ExecutionPolicy`, `DatabaseError`,
`DatabaseErrorCode`) live **only** in `src/core/database/policy.py`.
Import them from there (or from `src.core.database` / `connection.py`
re-exports — all three are the same objects, verified at runtime).

## 2. Where new code must NOT be placed

* Do **not** put value objects, protocols or error types in
  `connection.py`, adapters, the façade, or a new `compat.py` shim.
* Do **not** import `pymssql`, `openai`, `fastapi`, `streamlit`,
  `sqlite3`, or `src.memory.*` outside their designated layers (see
  §4 below) — the whole-tree scans fail the build.
* Do **not** import `src.agents.state` from any component other than
  the Orchestrator, the state model itself, or the façade.
* Do **not** import the façade (`src.agents.text_to_sql_agent`) from
  domain/contract/infrastructure code — only `src/api`, the UI entry
  points and the lazy package exports may.
* Do **not** make `src/agents/__init__.py` or
  `src/core/database/__init__.py` eager again: importing
  `src.agents.contracts` / `src.agents.state` /
  `src.core.database.policy` must stay free of heavy dependencies.

## 3. Canonical import locations

```python
# Value objects — canonical home
from src.core.database.policy import ExecutionPolicy, DatabaseError, DatabaseErrorCode

# Contracts / state
from src.agents.contracts import QueryResult, ...
from src.agents.state import AgentState, create_initial_state, ...

# Public façade (works via lazy PEP-562 exports, and equals the direct import)
from src.agents import TextToSQLAgent          # same object as below
from src.agents.text_to_sql_agent import TextToSQLAgent

# DB adapter (only for adapter/executor/facade code)
from src.core.database.connection import DatabaseConnection
```

`src.core.database.connection` re-exports the three policy value
objects for backwards compatibility; they are identity-identical to the
`policy.py` definitions (`test_backward_compatibility.py`,
`test_import_footprint.py::TestCanonicalOwnership`).

## 4. Allowed dependency directions (enforced)

```
api ─┐                       (façade only)
web ─┘
façade ── orchestrator ── adapters ── core (database/security/llm)
              │                │
              ▼                ▼
            state          contracts ── policy
```

* Contracts/state/policy are **leaf** surfaces: they must not import
  upward (API/UI, driver, provider SDK, memory, façade, orchestrator).
* The DB driver (`pymssql`) may only be imported in
  `src/core/database/connection.py`.
* Provider SDKs (`openai`) may only be imported in `src/core/llm/`.
* `fastapi` only in `src/api/`; `streamlit` only in `src/web/`;
  `sqlite3` only in `src/memory/`; `src.memory.*` only from the façade.
* The internal package graph must stay acyclic.

## 5. AgentState ownership rule (ADR-013)

**Only the Orchestrator writes AgentState.** Components receive inputs
via Protocols in `contracts.py` and return values; the Orchestrator
applies them with `state.with_*` methods. New components must never:

* import `src.agents.state`,
* accept or hold an `AgentState` instance,
* call `with_*` appliers.

Enforced by `tests/test_architecture/test_single_writer.py`
(whole-tree scan) and the per-module dependency tests.

## 6. Import-footprint expectations

Importing these modules in a fresh interpreter must load **zero** heavy
dependencies (`openai`, `pymssql`, `fastapi`, `streamlit`, `requests`,
`dotenv`, `pydantic`, `src.memory.manager`, the façade):

* `src.agents.contracts`
* `src.agents.state`
* `src.core.database.policy`
* `src.core.database` (the package itself)

Additionally, importing a lightweight surface must have **no runtime
side effects**: no sockets, no subprocesses, no file writes, no root
logging handlers, no pre-built `AgentState`/connection instances.
Logging stays lazy — `configure_logging()` runs on the first
`get_logger()` call. Enforced by
`tests/test_architecture/test_boundary_hardening.py` (CPython audit
hooks + filesystem diff in subprocesses) and
`test_import_footprint.py`.

## 7. Architecture tests — what fails when you break a rule

| Test file | Protects |
|---|---|
| `test_import_footprint.py` | lightweight import footprint (fresh subprocesses), lazy exports, canonical ownership of policy value objects, façade re-export identity |
| `test_boundary_hardening.py` | whole-tree SDK placement (pymssql/openai/fastapi/streamlit/sqlite3), façade & memory direction, import-time side effects, global-state/singletons |
| `test_dependencies.py` | per-module direct-import rules (contracts/orchestrator/adapters/state/policy) |
| `test_single_writer.py` | ADR-013 single-writer rule (whole-tree AgentState-import scan + `with_*` call scan) |
| `test_validation.py` | dependency cycles, protocol/signature conformance, error registry, value-object inventory |
| `test_backward_compatibility.py` | API schema, façade dict format, CLI constructor, evaluation adapter, DB re-export surface |

If one of these fails, the fix is to move the code (or the import), not
to add an exception. Exceptions require a new ADR.

## 8. Adding a new package/module without violating boundaries

1. Decide the layer from §1. New *domain* components go under
   `src/core/<subsystem>/`; new *shared value types* go in
   `contracts.py` (do not create a parallel contracts module).
2. Depend only on surfaces at or below your layer (§4). Access
   infra through Protocols + adapters, injected via constructor (see
   `OrchestratorDeps`) — never import a driver/SDK into domain code.
3. If you add a new public name to `src.agents`, add it to the
   hand-maintained `_EXPORTS` mapping in `src/agents/__init__.py`
   (explicit mapping only — no wildcard/scanned exports).
4. If you add a new `src` import edge to a protected lightweight
   module, expect the footprint/side-effect probes to fail; import a
   smaller module instead.
5. Run `uv run python -m pytest tests/test_architecture tests/test_agents -q`
   before pushing — it is fast and catches all boundary violations.

## 9. Intentional migration debt (do not "fix" silently)

These are approved, documented debts — removing them is future work,
not a drive-by cleanup:

* **~~Orchestrator → `SQLExtractor` → openai chain~~ — RESOLVED (Sprint 3 /
  Prompt 3.0B)**: the extractor imports no LLM module and the openai SDK
  loads only when a provider is actually constructed. The footprint
  probes now cover the LLM surfaces, the extractor, the façade and the
  Orchestrator (`tests/test_llm/test_import_purity.py`). The
  Orchestrator's remaining DB-driver load via
  `src.core.database.connection` is approved composition debt (A2), not
  provider leakage. Practical LLM recipes:
  [`llm-developer-guide.md`](llm-developer-guide.md).
* **Legacy `SQLValidator` wrapper**
  (`src/core/sql_extractor/validator.py` defines its own
  `SQLValidationError`/`SQLValidator` over the Security Core) —
  ADR-014 migration debt.
* **`os.getenv` in grandfathered legacy components** (LLM clients,
  `config_loader`, `logger`, `web/app.py`) — replaced gradually as
  components are rebuilt.
* **Compatibility re-exports** of the policy value objects from
  `connection.py` — intentional; canonical home is `policy.py`.
  Duplicate *definitions* are forbidden; re-exports are fine.

Any exception beyond this list requires a new ADR and an explicit
allowlist entry in the enforcing test with a comment pointing at it.
