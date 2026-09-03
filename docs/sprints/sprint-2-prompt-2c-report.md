# Sprint 2 — Prompt 2C Report: Contract Validation & Dependency Checks

**Sprint**: 2 — Prompt 2C (final step of "Interfaces & Contracts")
**Status**: Complete
**Scope**: Validation and hardening only — no new architecture layers.

---

## 1. Executive Summary

Prompt 2C verified that the Prompt 2B implementation (typed value
objects, runtime-checkable Protocols, typed errors, `AgentState`
appliers, `Orchestrator`, DI, adapters, compatibility façade)
conforms to the Prompt 2A contracts and that the architecture rules are
mechanically enforced.

Three pre-existing test failures left by Prompt 2B were diagnosed and
fixed (one real contract violation, one test false-positive, one
documentation gap). Two architecture-test blind spots were closed
(runtime conformance of the real `MemoryManager`, and the dependency
cycle test's precautionary cycle whitelist was removed). The contract
documentation was reconciled with the code (`ErrorStageTag` catalog was
missing from `interface-design.md`).

The full suite is green: **668 passed, 0 failed, 340 skipped**.
`compileall` and `git diff --check` pass. The evaluation harness was
validated structurally (offline); no live infrastructure run was
attempted or fabricated.

The interface/contract layer is considered trustworthy as the
foundation for the intelligence Sprints (Sprint 3+).

---

## 2. Contract Validation Results

All 17 target interfaces were audited (Step 2C.1/2C.2). Each exists in
`src/agents/contracts.py`, is a `@runtime_checkable` `Protocol`, is
exported through `__all__`, and is documented in
`docs/contracts/component-contracts.md` (verified mechanically by
`TestContractInventory` and `TestContractDocumentationConsistency`).

| Interface | Contract (2A) | Protocol (2B) | Runtime validation | Static (signature) validation | Issues |
|---|---|---|---|---|---|
| QuestionAnalyzer | ✔ | ✔ | test fakes / conformance | ✔ `TestProtocolMethodSignatures` | none |
| SchemaCatalog | ✔ | ✔ | test fakes | ✔ | none |
| SchemaRetriever | ✔ | ✔ | test fakes | ✔ | none |
| QueryPlanner | ✔ | ✔ | test fakes / happy path | ✔ | none |
| SQLGenerator | ✔ | ✔ | test fakes / happy path | ✔ | none |
| SQLValidator | ✔ | ✔ | test fakes | ✔ | none |
| SecurityValidator | ✔ | ✔ | `SecurityValidatorAdapter` isinstance | ✔ parity test | none |
| SemanticValidator | ✔ | ✔ | test fakes | ✔ | none |
| ComplexityValidator | ✔ | ✔ | test fakes | ✔ | none |
| SQLRepairer | ✔ | ✔ | stub-only (expected) | ✔ | repair wiring deferred (expected future absence) |
| QueryExecutor | ✔ | ✔ | `QueryExecutorAdapter` isinstance | ✔ parity test | none |
| ResultAnalyzer | ✔ | ✔ | test fakes | ✔ | none |
| PresentationPlanner | ✔ | ✔ | test fakes | ✔ | none |
| AnswerGenerator | ✔ | ✔ | test fakes | ✔ | none |
| AnswerValidator | ✔ | ✔ | test fakes | ✔ | none |
| MemoryManagerProtocol | ✔ | ✔ | **added**: real `MemoryManager` isinstance | ✔ | none |
| LLMRouter | ✔ | ✔ | test fakes | ✔ | none |

Contract-to-implementation comparison (Step 2C.2) found no missing
methods, signature mismatches, wrong optionality, or wrong State
ownership. The comparisons are encoded in
`TestProtocolMethodSignatures`, `TestAdapterSignatureParity`, and the
per-adapter source checks.

**One real defect found and fixed (2C.12/2C.27):** the Orchestrator
embedded natural-response prompt construction
(`_default_answer_fn` referenced `prompts.natural_response_prompt`),
which the 2A Orchestrator boundary forbids. The transitional default
callback moved to `src/agents/adapters.default_answer_fn` — the
adapters module is the sanctioned old↔new bridge. The Orchestrator now
contains no prompt-construction logic; behaviour is unchanged
(`OrchestratorDeps(answer_fn=None)` still produces the same default).

---

## 3. Typed Value Object Validation

Validated by `TestTypedValueObjectInventory` plus review:

* All value objects from the 2A contract are frozen dataclasses with
  the documented fields, defaults, and tuple/Mapping nesting.
* No contradictory duplicates exist:
  - `QuestionMetric` vs `PlanMetric` — intentionally distinct
    (user intent vs aggregation realisation), documented in docstrings.
  - `QuestionFilter` vs `PlanFilter` — same pattern, documented.
  - `Evidence` — single definition (contracts.py), shared by question
    references and answer citations.
  - `SecurityDecision` — single authoritative definition in
    `src/core/security/sql_security.py`; contracts.py imports it rather
    than redefining it.
* Error value objects are classes with typed fields; `ErrorStageTag`
  is the single stage-tag enum.

Known naming note (accepted, documented): `contracts.SyntaxError` and
`contracts.MemoryError` shadow the Python builtins *within that module
namespace*. They are the 2A-approved names; no builtin use occurs after
their definition in the module.

---

## 4. Error Contract Validation

* Every `TypedComponentError` subclass carries `code`, `message`,
  `user_message`, `stage`, `retryable`, `cause` (verified by
  `TestErrorContractValidation`).
* Sprint 1 error contracts are intact and unchanged:
  `SQLValidationError`, `SQLSecurityError`, `QueryExecutionError`,
  `DatabaseError`/`DatabaseConnectionError`, `ConfigurationError`,
  `LLMError` — all still defined in `src/utils/exceptions.py` /
  `sql_security.py` / `connection.py` with their original semantics.
* Error code registry (ADR-015, Step 2C.7): prefixes match
  `interface-design.md` §7.4; no duplicate codes within a prefix; every
  code is namespaced to its prefix (verified by
  `TestErrorCodeRegistry`). Future codes not yet used by code are
  acceptable and were **not** forced into fake usage.

---

## 5. Dependency Rule Verification

Verified mechanically (AST import scans) and independently re-scanned
by hand during this Prompt:

| Boundary | Rule | Result |
|---|---|---|
| Database | only `src/core/database/connection.py` imports `pymssql` | ✔ (the word "pymssql" appears in `contracts.py` only inside its MUST-NOT-import docstring) |
| LLM | provider SDKs (`openai`, `google-genai`) only in `src/core/llm/*` | ✔ |
| Memory | no domain component imports `src.memory` | ✔ |
| AgentState | `contracts.py` / `adapters.py` do not import `src.agents.state` | ✔ |
| API/UI | no domain component imports `fastapi` / `streamlit` / `src.api` / `src.web` | ✔ |
| Evaluation | no production module imports `evaluation.*` | ✔ |

Enforced by `tests/test_architecture/test_dependencies.py` and
`TestDependencyBoundaryHard` in `test_validation.py`.

---

## 6. Single-Writer Verification

Independent mechanical scan of all of `src/` for
`state.with_*`, `.transition_to(`, and `AgentState(` construction:

* `src/agents/orchestrator.py` — allowed (the single writer).
* `src/agents/state.py` — allowed (state module itself; appliers +
  `transition_to` implementation).
* `src/agents/text_to_sql_agent.py` — request boundary only:
  `create_initial_state(...)` and `state_to_api_response(...)`; no
  applier calls, no transitions.
* No other module in `src/` touches AgentState.

Enforced by `tests/test_architecture/test_single_writer.py`,
`TestSingleWriterMechanically`, and the AST-walk tests over new modules.

---

## 7. Dependency Cycle Results

DFS cycle detection over the internal import graph (`src/**`): **no
cycles exist**.

Prompt 2C hardening: the Prompt 2B cycle test whitelisted three
hypothetical cycle shapes (`src.agents ↔ src.agents`,
`src.agents ↔ src.core.database`, `src.agents ↔ src.memory`) as
"legitimate". The live graph contains none of them, so the whitelist
was removed — any future cycle now fails the suite
(`TestDependencyCycleDetection::test_no_dependency_cycles_among_internal_packages`).

The `src.agents` ↔ `src.core.security` / `src.core.database` /
`src.utils` edges are all one-directional (agents → core), as
documented.

---

## 8. Orchestrator Validation

* **Boundary (2C.12)**: the Orchestrator owns State transitions,
  coordinates dependencies, and handles typed errors. It contains no
  SQL-generation logic, schema retrieval, question understanding, DB or
  provider implementation, or presentation logic. After the Prompt 2C
  fix, it also contains no prompt-template construction.
* **Dependency injection (2C.13)**: `Orchestrator.__init__` takes only
  `deps`. The Orchestrator instantiates no `DatabaseConnection`,
  `LLMClient`, `MemoryManager`, `SQLExtractor`, or `SystemPrompts`
  (enforced by `TestDependencyInjection`). `OrchestratorDeps` defaults
  the `SecurityValidatorAdapter` and `ExecutionPolicy` — the deps
  container is the composition point, which is by design.
* **Retry ownership (2C.24)**: the Orchestrator contains no retry
  loops (AST-enforced). `_run_sql_repair` short-circuits when no
  repairer is wired.
* **State transitions (2C.25)**: all transitions flow through
  `transition_to` / appliers. The Prompt 2B "bypass" test was a
  substring false-positive on `state.pipeline_stage == PipelineStage.X`
  comparisons; it now uses an AST check for real assignments, which
  the Orchestrator passes without weakening the rule.

**Known transitional deviation (documented, not refactored — per Step
2C.24 "do not redesign the repair strategy yet")**: the legacy
`SQLExtractor.extract()` still contains its Sprint 1 bounded
correction retry (`max_retries=2`). This is the pre-existing legacy
generation path; retry ownership moves to the Orchestrator/SQLRepairer
when the generator path is wired in a future Sprint.

---

## 9. Adapter Validation

* `SecurityValidatorAdapter` — thin wrapper over `SQLSecurityValidator`;
  does not bypass security, does not mutate State, no DB/provider
  leakage. Signature parity with the Protocol is tested.
* `QueryExecutorAdapter` — thin wrapper over
  `DatabaseConnection.execute_query`; preserves the inline security
  check, row caps, timeouts, typed errors, fresh connection/cursor, and
  adds `duration_ms` timing. Returns typed `QueryResult`.
* **New in 2C**: `default_answer_fn` (moved here from the
  Orchestrator) — the transitional current-path answer callback. It is
  a parameterised function (no new imports into the adapters module),
  documented as removable when `AnswerGenerator` lands.

Enforced by `TestAdapterBoundaries` (thinness, no AgentState import,
no `with_*` calls) and `TestAdapterSignatureParity`.

---

## 10. Security Boundary Verification

The SQL → Security Core → Execution Guard → Database ordering is
preserved:

* `SecurityValidatorAdapter` wraps the authoritative
  `SQLSecurityValidator` unchanged.
* `QueryExecutorAdapter` relies on the documented inline security check
  inside `DatabaseConnection.execute_query` (§10,
  `component-contracts.md`); it does not add a path around it.
* The Orchestrator calls `security_validator.validate` before any
  execution and transitions security rejections straight to FAILED;
  execution only runs when `state.sql.is_validated`.
* The full Sprint 1 security test suite passes unchanged
  (`tests/test_security/`), plus `TestSecurityBoundaryRegression`.

---

## 11. Safe Execution Verification

The `QueryExecutor` contract preserves all Sprint 1 guarantees because
it delegates to the unchanged `DatabaseConnection.execute_query`:
`max_rows`, `query_timeout`, `max_fetch_batch` (fetch bound), typed
`DATABASE_*` errors, resource cleanup, per-call cursor isolation.
Covered by the existing `tests/test_database/` suite and
`TestSafeExecutionRegression`.

---

## 12. Observability Verification

* `TraceContext` / `StructuredLogger` remain cross-cutting
  infrastructure in `src/utils/logger.py`; domain contracts do not
  import logging infrastructure (only `log_event` per the rules doc).
* Orchestrator stage transitions preserve trace context: the façade
  owns `set_trace_context` / `reset_trace_context`, and every
  orchestrator event carries `trace_id` from State.
* No `print()` in components (enforced by `TestObservabilityRegression`).

---

## 13. API / CLI / Evaluation Compatibility

* **Public API frozen (2C.18)**: `POST /api/v1/ask`, `AskRequest`,
  `AskResponse` unchanged — verified by
  `TestPublicApiContract` / `test_backward_compatibility.py`.
  `src/api/routes.py` still depends only on the `TextToSQLAgent` façade.
* **CLI (2C.19)**: `run.py` / `run_ui.py` constructor and `ask()`
  contract unchanged (signature tests + manual smoke path).
* **Evaluation adapter (2C.19)**: `EvaluationAgentAdapter` still
  observes the real agent path and normalises the public response
  (test-verified). No production module imports `evaluation.*`.

---

## 14. Architecture Test Coverage

The Prompt 2B suite in `tests/test_architecture/` covers Steps 2C.1–2C.29
with AST/static analysis rather than hand-picked modules
(`COMPONENT_MODULES` enumerates every new module; the cycle test walks
every file under `src/`).

Blind spots found and closed in 2C:

1. Real `MemoryManager` was never runtime-checked against
   `MemoryManagerProtocol` → added
   `test_memory_manager_conforms`.
2. Cycle-test whitelist would have hidden future real cycles →
   removed.
3. Substring-based transition-bypass check produced false positives
   (and could therefore be trusted neither way) → replaced with an AST
   assignment check.
4. `interface-design.md` did not document every `ErrorStageTag` →
   documentation gap closed (§7.6 stage-tag catalog added).

---

## 15. Tests Added

| Test | Purpose |
|---|---|
| `test_validation.py::TestRuntimeProtocolConformance::test_memory_manager_conforms` (new) | Runtime Protocol conformance of the one existing production implementation |

Tests *modified* (no rule weakened):

| Test | Change |
|---|---|
| `test_orchestrator_does_not_bypass_transition_to` | substring check → AST assignment check (removes false positive; still rejects any direct `pipeline_stage` assignment) |
| `test_no_dependency_cycles_among_internal_packages` | removed precautionary cycle whitelist; any cycle now fails |

---

## 16. Test Results

Actual values:

```text
Baseline declared in the Prompt (post-2A):        333 passed
Repository state at start of 2C:                  664 passed, 3 failed, 340 skipped
New tests added in 2C:                            1
Pre-existing failures fixed in 2C:                3
Current total (non-skipped):                      668
Passed:                                           668
Failed:                                           0
Skipped:                                          340 (pre-existing; live DB/LLM/UI infra absent)
```

Note on the baseline discrepancy (Step 2C.27): the Prompt declared a
"333 passed" baseline, and the Prompt 2B report also ends at "333
passed". The repository, however, already contained the full Prompt 2B
implementation *plus* a 2,700-line `tests/test_architecture/test_validation.py`
that is not listed in the 2B report, with 3 of its tests failing. 2C
treated the repository as the source of truth, fixed the 3 failures,
and reports actual numbers above. The 2B report's test-count section
is stale relative to its own working tree.

---

## 17. Evaluation Results

```text
Live:      NOT RUN — no SQL Server / LLM infrastructure available
Offline:   PASS  — structural validation of docs/evaluation/initial-dataset.json
           (25 entries, unique ids, required fields present) and of the
           harness surfaces (evaluation.models / metrics / runner /
           adapters importable; EvaluationAgentAdapter intact)
Mocked:    evaluation unit tests (tests/test_evaluation/) — pass
```

No live agent / SQL Server / LLM results were fabricated.

---

## 18. Changes Made

1. `src/agents/orchestrator.py` — removed `_default_answer_fn` (and the
   `SystemPrompts` / `LLMClient` imports it required); the default
   answer callback is now built by `src.agents.adapters.default_answer_fn`.
   Behaviour unchanged.
2. `src/agents/adapters.py` — added transitional `default_answer_fn`
   adapter (parameterised; no new module imports).
3. `tests/test_architecture/test_validation.py` — AST-based
   transition-bypass check; removed cycle whitelist; added
   `test_memory_manager_conforms`.
4. `docs/contracts/interface-design.md` — added §7.6 "Error stage tags
   (`ErrorStageTag`)" catalog (all 17 stage tags documented); old §7.6
   renumbered to §7.7.
5. `docs/architecture/dependency-rules.md` — §10 note: 2C hardening
   items (cycle whitelist removal, MemoryManager conformance test, AST
   bypass check, answer-fn relocation).

No unrelated refactoring was performed (Step 2C.34 respected).

---

## 19. Files Created

* `docs/sprints/sprint-2-prompt-2c-report.md` (this report)

---

## 20. Files Modified

* `src/agents/orchestrator.py`
* `src/agents/adapters.py`
* `tests/test_architecture/test_validation.py`
* `docs/contracts/interface-design.md`
* `docs/architecture/dependency-rules.md`

---

## 21. Deviations from Prompt 2A/2B

| Deviation | Explanation |
|---|---|
| Default answer callback moved from `orchestrator.py` to `adapters.py` | 2A forbids prompt construction in the Orchestrator; 2B left `_default_answer_fn` inside it (with a failing architecture test). 2C fixes the violation; the adapters module is the sanctioned transitional bridge. Behaviour and public API are unchanged. |
| Cycle-test whitelist removed | 2B's cycle test accepted three hypothetical cycle shapes. None exist in the live graph; the whitelist was precautionary and weakened enforcement. Documented in `dependency-rules.md` §10. |
| Transition-bypass test rewritten (substring → AST) | The 2B test failed on `state.pipeline_stage ==` comparisons — a false positive, i.e. a test bug, not an architecture violation. The AST version is strictly stronger for real assignments. |
| `interface-design.md` gained §7.6 stage-tag catalog | Required by the 2B documentation-consistency test (`test_interface_design_documents_every_error_stage`) which 2B left failing. |
| SQLExtractor retry retained | 2A says "components do not retry", but Step 2C.24 explicitly forbids redesigning the repair strategy in this Prompt. The legacy bounded retry is documented as transition debt (see §8). |

---

## 22. Known Limitations

* Full static type checking (mypy/pyright) is **not** configured in the
  repository (dev tooling is pytest only). Per Step 2C.4 no toolchain
  was introduced; the strongest practical verification is the
  AST-based signature/parity tests plus `compileall`. Introducing a
  type checker is deferred to CI/future work.
* `runtime_checkable` Protocol checks verify attribute presence only;
  signature depth comes from the dedicated parity tests.
* `SQLRepairer` wiring into the Orchestrator is documented but not
  implemented (expected future absence; repair strategy redesign is
  out of scope).
* Legacy `SQLExtractor` retains its bounded retry until the generator
  path replaces it (transition debt).
* 340 tests skip in environments without live SQL Server / LLM /
  Streamlit infrastructure (pre-existing behaviour).
* Orchestrator `_run_sql_generation` (generator path) performs a
  security validation at generation time and `_run_sql_validation`
  validates again — a read-only, harmless duplication in the
  transitional dual-path design.

---

## 23. Remaining Work for Future Sprints

* Wire `SQLRepairer` into the Orchestrator's repair stage; move retry
  ownership fully out of `SQLExtractor`.
* Sprint 3: implement `SchemaCatalog` / `SchemaRetriever` against the
  reusable contract tests (`TestReusableContractPatterns`).
* Later sprints: `QuestionAnalyzer` intelligence, `QueryPlanner`,
  `SQLGenerator`, validators, `ResultAnalyzer`, `PresentationPlanner`,
  `AnswerGenerator`/`AnswerValidator`, `LLMRouter` — all behind the
  existing Protocols.
* Introduce a static type checker in CI (mypy or pyright) and wire it
  to the contracts.
* Rewrite the stale Prompt 2B report test-count section (noted in §16).

---

## 24. Sprint 2 Prompt 3 Recommendation

**Ready.** The contract layer is validated, the dependency rules are
mechanically enforced with no known blind spots, the single-writer rule
holds, all boundaries regress green, and the public API/CLI/evaluation
surfaces are frozen and tested. No additional contract work is required
before starting **Sprint 2 / Prompt 3 — Dependency Boundaries & Package
Architecture**. The only carry-over item (SQLExtractor's transitional
retry) is documented transition debt that Prompt 3 does not depend on.

---

## Verification Checklist (Definition of Done)

```text
[x] All target interfaces audited
[x] Contract-to-implementation comparison completed
[x] Runtime Protocol checks added where meaningful
[x] Static signature verification performed (AST parity tests; no toolchain introduced)
[x] Typed value objects validated
[x] Error contracts validated
[x] Error registry validated
[x] Single-writer rule mechanically verified
[x] Dependency boundaries mechanically verified
[x] Dependency cycles checked (and enforcement hardened)
[x] Forward dependency rules checked
[x] Orchestrator boundary validated (one violation fixed)
[x] Dependency injection validated
[x] Adapter boundaries validated
[x] Security boundary regression passes
[x] Safe Execution regression passes
[x] Observability regression passes
[x] Public API contract remains unchanged
[x] CLI compatibility remains intact
[x] Evaluation Adapter remains intact
[x] Contract tests exist for reusable future implementations
[x] Orchestrator happy path tested
[x] Orchestrator failure paths tested
[x] Retry ownership verified (legacy exception documented)
[x] State transition rules verified
[x] Serialization boundaries verified
[x] Architecture test coverage strengthened
[x] Full regression suite passes (668 passed / 0 failed)
[x] Evaluation Harness remains functional (offline/structural)
[x] compileall passes
[x] git diff --check passes
[x] Documentation is reconciled
[x] Sprint 2 Prompt 2C report is complete
```
