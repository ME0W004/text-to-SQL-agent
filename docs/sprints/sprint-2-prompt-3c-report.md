# Sprint 2 / Prompt 3C Report — Boundary Enforcement, Integration & Regression Hardening

**Date**: 2026-09-03
**Stage**: 3C (boundary enforcement, integration & regression hardening)
**Decision record**: [`ADR-016`](../adr/ADR-016-package-architecture.md)
**Prior stage reports**: [`3A`](sprint-2-prompt-3a-report.md), [`3B`](sprint-2-prompt-3b-report.md)
**Developer guide produced**: [`docs/architecture/developer-package-guide.md`](../architecture/developer-package-guide.md)

## 1. Executive Summary

Stage 3C made the approved ADR-016 package architecture automatically
enforceable against the **whole source tree** and future modules. No
production code was changed — all changes are architecture tests and
documentation. The previously per-file direct-import rules now run as
whole-tree scans (pymssql / openai / fastapi / streamlit / sqlite3 may
only be imported by their designated layer; the façade and
`src.memory` have sanctioned importer sets; `AgentState` imports are
scanned tree-wide for the ADR-013 single-writer rule). The transitive
import-footprint probes were extended to `src.core.database.policy`
and the `src.core.database` package itself, and a new behavioral
side-effect gate (CPython audit hooks + filesystem diff in fresh
subprocesses) proves importing the lightweight surface opens no
sockets, spawns no processes, writes no files, adds no root logging
handlers and constructs no live state. Compatibility re-exports are
identity-verified through the full package → policy → connection
chain, and duplicate definitions of the policy value objects are
rejected anywhere under `src/`. A developer-facing package guide
documents placement rules, boundaries, enforcement points and
intentional migration debt. Full regression: **720 passed, 340
skipped, 0 failed** (baseline 688 — 32 new architecture tests, zero
baseline tests changed or removed).

## 2. Baseline Before Changes

| Check | Result |
|---|---|
| `uv run python -m pytest tests -q` | **688 passed, 340 skipped, 0 failed** (12.62 s) |
| `uv run python -m pytest tests -q -rs` | **688 passed, 340 skipped, 0 failed** (9.80 s) — all 340 skips from the documented protocol-matrix parametrization in `test_validation.py` (249 reason lines, multiplicities sum to 340) |
| `uv run python -m pytest tests/test_architecture tests/test_agents -q` | **587 passed, 340 skipped, 0 failed** (8.80 s) |
| `uv run python -m compileall -q src evaluation scripts tests` | **OK**, no errors |

Matches the approved Stage 3B report exactly. No discrepancy.

## 3. Architecture Enforcement Changes

| Path | What changed | Rule enforced | Why necessary |
|---|---|---|---|
| `tests/test_architecture/test_boundary_hardening.py` | **New** (23 tests). Whole-tree AST scans for SDK/layer placement; façade & memory importer allowlists; lightweight-surface direction check; fresh-subprocess import-time side-effect probes (audit hooks + fs diff); global-state/singleton boundary checks. | ADR-016 layer boundaries; dependency direction; import-time purity; ADR-013 (no shared state) | 3B tests covered only named files; a *new* module in the wrong layer would previously pass unnoticed |
| `tests/test_architecture/test_import_footprint.py` | `LIGHTWEIGHT_MODULES` += `src.core.database.policy`; new `test_database_package_import_stays_lazy`; new `TestFacadeReExportCompatibility` (subprocess identity check for `from src.agents import TextToSQLAgent`); duplicate-definition scan extended from 3 files to all of `src/`; `_probe` results cached (`lru_cache`) | Transitive footprint (§4 of prompt); canonical ownership; façade compatibility | Prompt 3C §4 requires policy coverage; package-level laziness and the façade path were untested; a duplicate VO in any future module must fail |
| `tests/test_architecture/test_single_writer.py` | New whole-tree scan `test_no_production_module_imports_agent_state_outside_the_single_writer` with TYPE_CHECKING-guard detection | ADR-013 single-writer | Existing rule enumerated only contracts/adapters; future modules were unprotected |
| `tests/test_architecture/test_backward_compatibility.py` | New `TestDatabasePackageExportSurface` (4 tests): package→policy→connection identity chain, `dir()`, unknown-attribute `AttributeError`, `connection.__all__` retains the three re-exported names | Compatibility re-export identity policy (§7 of prompt) | The `src.core.database` package export path had no identity/dir/error-behavior coverage |
| `docs/architecture/developer-package-guide.md` | **New** — practical developer guide: placement table, forbidden locations, canonical imports, dependency directions, AgentState rule, footprint expectations, test-to-rule map, new-module checklist, intentional debt list | Documentation criterion (§14) | Required deliverable |
| `docs/architecture/package-architecture.md` | Status note on §10 (enforcement plan now implemented; links the guide) | Doc/implementation consistency | Prevent contradiction between plan and state |
| `docs/architecture/dependency-rules.md` | Companion link to the developer guide | Doc consistency | Same |

**No `src/` production file was modified in this stage.**

## 4. Dependency Boundary Tests

| Surface | Coverage | Enforcing test |
|---|---|---|
| contracts | No pymssql/SDK/API/memory/state/driver-module imports (direct) + no heavy stack transitively (subprocess) + not defined upward | `test_dependencies.py::TestContractsModuleBoundaries`, `test_import_footprint.py`, `test_boundary_hardening.py::TestWholeTreeInternalBoundaries` |
| state | Same lightweight surface rules; must not import orchestrator; no live instances at import | `TestLightweightAgentModules`, `test_validation.py`, `test_boundary_hardening.py` |
| policy | Driver-free, minimal imports (exact allowlist), footprint probe | `TestPolicyModulePurity`, `test_import_footprint.py` |
| database boundary | `pymssql` importable **only** in `src/core/database/connection.py` — whole tree, future files included | `test_boundary_hardening.py::test_pymssql_only_imported_by_database_driver_adapter` |
| provider boundary | `openai` importable **only** under `src/core/llm/` — whole tree. Legacy Orchestrator→SQLExtractor→OpenAI chain left as documented debt (not solvable at the import-of-orchestrator level; probes protect only the lightweight surface) | `test_boundary_hardening.py::test_openai_sdk_only_imported_by_llm_provider_layer` |
| API/UI boundary | `fastapi` only in `src/api/`; `streamlit` only in `src/web/`; API imports only the façade (existing) | `test_boundary_hardening.py`, `test_dependencies.py::TestApiModuleDoesNotImportOrchestrator` |
| memory boundary | `src.memory.*` importable only from the façade (and the memory package itself) — whole tree; `sqlite3` only in `src/memory/` | `test_boundary_hardening.py` |
| dependency cycles | DFS cycle detection over the real internal package graph, no whitelist; `policy.py` introduces no reverse dependency (policy imports only stdlib + `src.utils.exceptions`) | `test_validation.py::TestDependencyCycleDetection` |

## 5. Transitive Import Footprint

- **Modules tested (fresh subprocess, `sys.modules` inspection):**
  `src.agents.contracts`, `src.agents.state`, `src.core.database.policy`
  (new), `src.core.database` package (new).
- **Prohibited modules:** `openai`, `pymssql`, `fastapi`, `streamlit`,
  `langchain` (prefix), `requests`, `dotenv`, `pydantic`,
  `src.memory.manager`, `src.agents.text_to_sql_agent` (+ prefix match
  for `langchain`, `openai`, `google.genai`).
- **Subprocess strategy:** one fresh interpreter per protected module
  (`python -c` probe), importing exactly the target and dumping the
  heavy/`src` module sets as JSON; results cached per module per run
  (`lru_cache`) so the parametrized assertions share one probe.
  Static AST scans are kept but are not relied upon for transitivity.
- **Actual footprint results:** all probes report `heavy == []`;
  `contracts` does not load `src.core.database.connection`; the
  `src.core.database` package import loads neither the driver adapter
  nor any heavy module. Façade re-export identity verified in a fresh
  subprocess (`src.agents.TextToSQLAgent is
  src.agents.text_to_sql_agent.TextToSQLAgent`).
- **Suite-time impact:** see §17.

## 6. Import-Time Side-Effect Validation

Method: fresh subprocess per target with a CPython **audit hook**
(`socket.connect`, `socket.getaddrinfo`, `socket.bind`,
`socket.sendto`, `urllib.Request`, `subprocess.Popen`, `os.system`,
`os.exec`, `os.spawn`, write-mode `open` excluding `__pycache__`/`.pyc`)
plus a before/after working-directory file diff, root-log-handler
delta, and a scan for live `AgentState` instances. Targets: the three
lightweight modules + the `src.core.database` package.

| Concern | Result |
|---|---|
| Database connection / SQL execution | **Verified absent** — no socket/process events; driver module not even loaded (§5) |
| LLM/provider client initialization | **Verified absent** — `openai` not loaded; no network audit events |
| Network access | **Verified absent** — no socket/urllib audit events |
| API server / UI framework initialization | **Verified absent** — `fastapi`/`streamlit` not loaded |
| AgentState mutation / shared request state | **Verified absent** — no live `AgentState` instances at module level |
| Disk writes / global singletons | **Verified absent** — no new files; no root logging handlers added |
| Intentionally allowed static initialization | module-level constants, dataclasses, enums, Protocol definitions, the hand-maintained `_EXPORTS` mappings, and the lazy `configure_logging()` machinery (runs on first `get_logger()` call, not at import) — explicitly *not* forbidden |

## 7. Global State / Singleton Validation

- **AgentState isolation:** no module-level `AgentState` anywhere
  (`test_state_module_holds_no_prebuilt_state`); per-request creation
  and concurrent isolation already covered by
  `tests/test_agents/test_state_isolation.py` (green).
- **DB connection/cursor isolation:** no `DatabaseConnection`
  instance on the `src.core.database` package; existing lifecycle
  tests (cursor close, connection close) green — no shared cursor or
  connection exists in module state.
- **Mutable global state:** `src.agents` package namespace may hold
  only classes/functions/submodules + the static `_EXPORTS` mapping
  (Python's import machinery submodule bindings are expected and
  allowed; they are modules, not mutable data).
- **Singleton boundaries:** TraceContext remains a `contextvars`-based
  per-context value (covered by `test_state_boundaries.py`); no
  import-time service initialization (§6).
- **Intentionally allowed global state:** the lazy export mappings,
  `TYPE_CHECKING` blocks, and `logger._configured` flag — documented,
  benign, configuration-level.

## 8. Canonical Ownership & Compatibility

- **Policy definitions:** `ExecutionPolicy`, `DatabaseError`,
  `DatabaseErrorCode` defined only in `src/core/database/policy.py`.
- **Duplicate-definition protection:** AST `ClassDef` scan across
  **every** `src/**/*.py` (was: 3 named files) — any future duplicate
  class definition fails; re-exports are the only permitted form.
- **Identity checks (runtime):** `policy.X is connection.X` for all
  three names; `contracts.ExecutionPolicy is policy.ExecutionPolicy`;
  **new:** full chain `src.core.database.X is policy.X is
  connection.X`, plus `src.agents.TextToSQLAgent` identity in a fresh
  subprocess.
- **Package exports:** `__all__`/`dir()`/lazy resolution/unknown
  attribute `AttributeError` verified for both `src.agents` and
  `src.core.database`; `connection.__all__` still contains the three
  re-exported names.
- **No duplicate compatibility implementations** added.

## 9. AgentState Single-Writer Validation

Three layers of protection (ADR-013):

1. **Whole-tree import scan (new)** — no module under `src/` may
   import `src.agents.state` at runtime except `state.py` (definition),
   `orchestrator.py` (sole writer) and `text_to_sql_agent.py` (façade
   reading final state). `TYPE_CHECKING`-guarded imports are detected
   and only tolerated in `src/agents/__init__.py` (erased at runtime;
   footprint tests prove the package stays lightweight).
2. **Call-site scan (existing, kept)** — only `orchestrator.py` may
   invoke `state.with_*` appliers among component modules; the
   orchestrator must use at least one.
3. **Behavioral (existing, kept)** — dependency-injection constructor
   test proving the orchestrator holds injected deps by reference and
   creates no infrastructure; per-request state isolation suite.

## 10. API / CLI / Application Compatibility

- **API:** `/api/v1/ask` exercised via FastAPI `TestClient` in
  `tests/test_integration/test_sprint1_path.py` (green);
  `AskRequest`/`AskResponse` field schemas asserted in
  `test_backward_compatibility.py` (green); `state_to_api_response`
  success/failure shapes unchanged (green).
- **Agent façade:** `from src.agents import TextToSQLAgent` verified
  in a fresh subprocess (identity + defining module);
  `TextToSQLAgent.ask()` dict format test green.
- **CLI:** `run.py` path — constructor-signature compatibility test
  green; evaluation CLI tests (`tests/test_evaluation/test_cli.py`)
  green.
- **Database compatibility:** `execute_query()` surface and policy
  value objects via `connection.py` re-exports identity-verified.
- **Evaluation:** `EvaluationAgentAdapter` normalization + runner +
  metrics suites green.

## 11. Security Regression Validation

`tests/test_security` (+ security cases inside integration) — green,
0 failed. Security Core remains authoritative
(`src/core/security/sql_security`); whole-tree scans confirm no module
bypasses the validator layer; no execution path was modified in this
stage (no production code touched), so fail-closed behavior is
structurally unchanged.

**Security behavior changed: NO**

## 12. Database Execution Regression Validation

`tests/test_database` green (0 failed). `ExecutionPolicy` semantics,
timeouts, row limits, fetch batching, overflow handling, typed error
mapping and cursor/connection lifecycle are all covered by the
existing suite and unchanged; the canonical definitions were already
moved in 3B and this stage added only identity/duplicate guards.

**Database execution behavior changed: NO**

## 13. Observability Regression Validation

`tests/test_observability` (incl. `test_integration.py` trace-header
tests) green. TraceContext propagation, structured logging, SQL
fingerprints, sanitization and API trace headers unchanged. The new
side-effect tests *add* an architecture-level guarantee: importing
protected modules configures no logging handlers (logging stays
lazy). No observability code was modified.

**Observability behavior changed: NO**

## 14. Documentation Updates

- **`docs/architecture/developer-package-guide.md` (new)** — the 12
  required topics: where code belongs (placement table), forbidden
  locations, canonical import locations, allowed dependency
  directions (diagram), database boundary, LLM/provider boundary,
  memory boundary, AgentState ownership rule, compatibility re-export
  policy, architecture-test expectations (test→rule table), new-module
  checklist, intentional migration debt list.
- **`docs/architecture/package-architecture.md`** — §10 status note:
  enforcement plan items implemented in 3B/3C; link to the guide.
- **`docs/architecture/dependency-rules.md`** — companion link to the
  guide.
- No contradictions with ADR-016 / package-architecture.md introduced
  (guide defers to ADR on conflicts).

## 15. Migration Debt

### Pre-existing

- Legacy Orchestrator → `SQLExtractor` → OpenAI dependency chain
  (heavy orchestrator import) — intentional, ADR-011 defers to
  LLMRouter/SQLGenerator.
- Legacy `SQLValidator` wrapper in
  `src/core/sql_extractor/validator.py` — ADR-014 debt.
- Direct `os.getenv` in grandfathered components (LLM clients,
  config_loader, logger, web/app, policy-from-environment) — replaced
  gradually.
- Compatibility re-exports from `connection.py` — permanent until
  consumers migrate.

### Introduced by this task

- None.

### Intentionally deferred

- Whole-tree provider-boundary scan does **not** fail on the
  orchestrator's *transitive* openai load (by design — the debt above;
  the probes protect only the lightweight surface, exactly as the
  prompt prescribes).
- Side-effect/footprint probes intentionally cover 4 high-value
  surfaces only (prompt: keep the protected list small).

## 16. Test Results

| Command | Result | Duration |
|---|---|---|
| `uv run python -m pytest tests -q` (final) | **720 passed, 340 skipped, 0 failed** | 24.0 s (cold) / 20.2 s (warm) |
| `uv run python -m pytest tests -q -rs` | 720 passed, 340 skipped; all skips = documented protocol matrix (249 reason lines × multiplicities = 340) | 20.2 s |
| `uv run python -m pytest tests/test_architecture tests/test_agents -q` | **619 passed**, 340 skipped | 17.4 s |
| `uv run python -m pytest tests/test_security tests/test_database tests/test_observability tests/test_evaluation tests/test_integration -q` | **101 passed**, 0 failed | 3.3–4.1 s |
| API tests (`test_sprint1_path.py` TestClient + schema tests) | passed (in suites above) | — |
| CLI tests (`test_evaluation/test_cli.py`, constructor test) | passed (in suites above) | — |
| `uv run python -m compileall -q src evaluation scripts tests` | OK, no errors | <2 s |
| Static/format checks | **none configured in the repository** (pyproject has only hatch build config); none introduced | — |

## 17. Performance / Test-Suite Cost

- New architecture tests: +32 tests. Full suite: **12.6 s → ~20–24 s**
  (≈ +8–11 s), i.e. the architecture package grew from ~8.8 s to
  ~20.4 s.
- Cost driver: 10 fresh subprocess probes (~0.8–1.2 s each on
  Windows: interpreter startup dominates) — 5 footprint/identity
  probes + 4 side-effect probes + 1 façade-identity probe. AST
  whole-tree scans cost <0.5 s combined.
- Redundancy controls applied: probe results cached per module per
  run (`lru_cache` — cut the side-effect class from 31.5 s to 10.2 s);
  each protected module probed exactly once per assertion family;
  footprint vs side-effect probes kept separate because they assert
  different things (`sys.modules` contents vs runtime events).

## 18. Scope Review

Every changed file reviewed against Stage 3C intent:

- **unrelated feature work introduced: NO**
- **package redesign performed: NO** (tests + docs only; no `src/` file touched this stage)
- **public API changed: NO** (no production code modified; API surface re-verified)
- **runtime behavior changed: NO**

The `src/` modifications visible in git status are the approved,
pre-existing 3A/3B work (uncommitted before this stage began); this
stage added only the 4 test files + 3 doc files listed in §3.

## 19. Deviations From Stage 3B

**NONE.** No production module, export, or behavior from the approved
3B result was altered.

## 20. Risks & Known Limitations

- **Subprocess-probe runtime sensitivity:** footprint/side-effect
  probes cost real seconds and depend on interpreter startup; on very
  slow CI the architecture package may add ~10 s (measured, bounded,
  cached per run).
- **AST scans see only static imports:** dynamic `importlib` string
  imports are invisible to the whole-tree scans — mitigated for the
  protected surface by the runtime subprocess probes (`sys.modules`).
- **Audit-hook coverage:** the side-effect gate observes CPython audit
  events; exotic side effects (e.g., C-extension work without audit
  events) are out of reach — no such dependency exists in the
  lightweight surface today.
- **Windows-path assumptions:** probes run with `cwd=PROJECT_ROOT`;
  the fs-diff walks the repo root (excludes `__pycache__`), verified
  on Windows (Git Bash) — the environment of record.
- **Provider-boundary transitivity:** intentionally not enforced for
  the orchestrator (documented debt, §15) — a future edit that makes
  the orchestrator import openai *directly* still fails the scan; only
  the existing transitive chain is tolerated.

## 21. Final Status

**PASS**

- Runtime behavior changed: **NO**
- Public API compatibility changed: **NO**
- Security behavior changed: **NO**
- Database execution behavior changed: **NO**
- AgentState ownership changed: **NO**
- Observability behavior changed: **NO**

## 22. Recommendation

Recommended next architectural step: **build the LLMRouter (ADR-011)
as the first Sprint 3 component**, because it directly retires the
largest documented debt — the legacy Orchestrator → SQLExtractor →
OpenAI chain. Concretely: implement `LLMRouter` against the existing
`contracts.py` Protocol in `src/core/llm/` (the only layer allowed to
import provider SDKs, now enforced tree-wide), inject it via
`OrchestratorDeps`, then shrink `orchestrator.py`'s imports to the
lightweight surface — at which point `src.agents.orchestrator` can be
added to the protected footprint list, and the deferred-debt exception
in `test_boundary_hardening`/docs can be deleted. After that, proceed
to SQLGenerator behind the same pattern.
