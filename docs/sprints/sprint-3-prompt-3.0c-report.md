# Sprint 3 / Prompt 3.0C Report — LLM Boundary Enforcement, Integration & Regression Hardening

**Date**: 2026-09-04 · **Branch**: `new-ar` · **Stage**: 3.0C (final stage of Sprint 3 / Prompt 3.0)
**Final status**: **PASS WITH DEBT** (§30)

## 1. Executive Summary

Stage 3.0C made the Sprint 3 LLM boundary machine-enforced and regression-safe **without changing any production code**. Verified: provider-SDK leakage is absent repo-wide; the Orchestrator/extractor/contracts boundaries hold; canonical LLM type ownership is enforced; provider construction is lazy and import-safe; the decisive provider-substitution test runs the full Orchestrator pipeline on a fake provider with zero provider SDKs loaded; the F3 openai-chain debt is **fully resolved** and its removal is pinned by fresh-subprocess probes; documentation is reconciled. Full regression: **849 passed / 340 skipped / 0 failed**, compileall clean.

## 2. Baseline Before 3.0C

| Metric | Value |
|---|---|
| passed | **824** |
| skipped | **340** |
| failed | **0** |
| compileall | clean (exit 0) |

**Reconciliation with the 3.0B report**: exact match (824/340/0, compileall clean).

**Skip audit (`-rs`, not assumed from the previous report)**: all 340 skips originate from one parametrized compatibility-matrix test, `tests/test_architecture/test_validation.py:261` (`test_protocol_method_parameters`), which pairs every expected method name against all 18 component Protocols and skips the pairs where the method is legitimately not part of that protocol. Distribution: 17–22 skips per protocol (e.g. SchemaRetriever 22, SQLRepairer 22, MemoryManagerProtocol 18), summing to exactly 340. **There are zero environment-dependent, live-provider, or database-dependent skips — the suite is fully offline-deterministic.**

## 3. Final LLM Architecture

```
Application / Orchestrator        (no provider imports; deps.llm_router)
        │ generate(prompt) -> str (task-bound legacy seam)
        ▼
LLMClient adapter                 (src/core/llm/client.py)
        │ generate(prompt, *, task, policy, budget) -> LLMResult
        ▼
LLMRouter                         (src/core/llm/router.py)
        │ BaseLLMClient interface (src/core/llm/base.py)
        ▼
Provider implementations          (openai/groq/gemini/ollama_client.py — only SDK imports)
        ▼
External provider SDKs
```

Application → LLM contract only; provider adapters → SDK only, never upward.

## 4. Provider Boundary

Repo-wide scan for `openai`, `google.genai`, `langchain_groq`, `langchain_ollama` (and `requests`) across `src/`: **all matches are exactly the four approved provider modules**; zero leakage elsewhere.

| SDK | Exact location | Enforcement | Result |
|---|---|---|---|
| `openai` | `src/core/llm/openai_client.py` only | whole-tree AST scan (`test_boundary_hardening.py::test_openai_sdk_only_imported_by_llm_provider_layer`) + subprocess probes | PASS |
| `langchain_groq` | `src/core/llm/groq_client.py` only | same mechanism | PASS |
| `google.genai` | `src/core/llm/gemini_client.py` only | same mechanism + purity probes | PASS |
| `langchain_ollama` | `src/core/llm/ollama_client.py` only | same mechanism | PASS |

Additional static rules (`tests/test_architecture/test_llm_boundary.py`): `base.py`/`errors.py`/`policy.py` are provider-neutral with minimal imports; `router.py` has no module-level provider/SDK imports (lazy factories only); provider clients import nothing upward (state/memory/API/UI/DB).

## 5. Orchestrator Boundary

`src/agents/orchestrator.py` imports (verified by inspection): stdlib only + `src.agents.{contracts,state,adapters}`, `src.core.database.connection`, `src.core.sql_extractor.{extractor,validator}`, `src.core.security.sql_security`, `src.utils.{exceptions,logger}`. It imports **no provider SDK and no provider implementation** (AST-enforced by `test_dependencies.py::test_does_not_import_provider_sdks`; runtime-proven by the footprint probe, §10).

Final footprint: provider SDKs **none**; provider implementations **none**; DB driver module **loads** (pymssql via the approved `database.connection` composition import — pre-existing, documented as A2 composition debt, not provider leakage); UI/API **none**; memory **none** (`src.memory.manager` not loaded — memory arrives by injection).

## 6. SQLExtractor Boundary

`extractor.py` imports no `src.core.llm.*` module, no `LLMClient`, no provider SDK (AST-pinned by `TestSqlExtractorIsolation`; fresh-subprocess probe shows its import path loads **zero** `src.core.llm` modules and zero SDKs). The `generate(prompt) -> str` seam and all existing fakes are preserved; SQL extraction logic untouched.

## 7. Contract Purity

`contracts.py` imports no provider SDK (AST rule + footprint probe). Canonical ownership — exactly one definition each (whole-tree AST scan `test_definitions_live_only_in_policy_leaf` + identity test `test_contracts_reexports_canonical_objects`):

| Concept | Canonical home | Compatibility surface |
|---|---|---|
| `LLMPolicy` | `src/core/llm/policy.py` | re-exported by `contracts.py` (identity-pinned) |
| `LLMBudget` | `src/core/llm/policy.py` | re-exported |
| `LLMResult` | `src/core/llm/policy.py` | re-exported |
| `Usage` | `src/core/llm/policy.py` | re-exported |
| LLM errors (provider taxonomy) | `src/core/llm/errors.py` | — |
| `LLMRouter` Protocol, `LLMRouterError` | `src/agents/contracts.py` | — (implemented by `router.py`, never re-declared: `test_router_module_defines_no_second_protocol`) |

The leaf exists so `src.core.llm` never imports `src.agents` (cycle rule — AST-enforced by `test_llm_layer_never_imports_agents_package`).

## 8. Router Behavior

* **Provider order**: default `openai → groq → ollama` (approved); Gemini registered but excluded (pinned). Configured order via `LLM_PROVIDER_ORDER`; explicit selection via `LLMPolicy.provider` / `fallback_chain`. Deterministic — same inputs, same order.
* **Fallback**: only for classified provider failures (`Unavailable` → skip without retry; `Timeout`/`RateLimited`/generic → bounded retry then fallback). `LLMConfigurationError` aborts immediately — never re-routed (pinned).
* **Attempt budget**: bounded by chain × `max_attempts_per_provider` (default 1, floor 1); no `while`-loops; no unbounded retry (audited, §13/§24 of the task — extractor's legacy correction `while` loop is intentionally deferred, unchanged).
* **Timeout**: `LLMBudget.timeout_ms` → per-request `timeout_seconds` (pinned: 1500 ms → 1.5 s). Provider limitations documented: Groq/Ollama ignore overrides; Gemini maps to `http_options.timeout`; OpenAI maps to the SDK `timeout` kwarg.
* **Temperature/max_tokens**: propagate where supported; legacy defaults 0.1/500 preserved (pinned).
* **Result**: canonical `LLMResult` (text/provider/model/`Usage`/latency/task/attempt/fallback metadata); no SDK response objects cross the boundary.
* **Error normalization**: untyped provider exceptions → `LLMProviderError` (cause preserved); configuration errors pass through unwrapped.

## 9. Provider Conformance

All four providers subclass `BaseLLMClient` (runtime check), match `BaseLLMClient.generate` **by signature** (`inspect.signature` parity — not merely `runtime_checkable`), expose `name`/`model`, implement cheap config-based `is_available()`, and translate SDK exceptions to the neutral taxonomy with preserved `__cause__`. The `LLMRouter` implementation satisfies the canonical Protocol (`runtime_checkable` isinstance **and** signature parity against `contracts.LLMRouter.generate`).

## 10. Import Footprint (fresh-subprocess probes, `tests/test_llm/test_import_purity.py`)

| Surface | Provider SDKs / HTTP | Provider impls | DB driver | API/UI | Memory | Facade |
|---|---|---|---|---|---|---|
| `agents.contracts` | none | none | none | none | none | none |
| `agents.state` | none | none | none | none | none | none |
| `database.policy` | none | none | none | none | none | none |
| `llm.policy` / `llm.errors` / `llm.base` | none | none | none | none | none | none |
| `llm.router` | none | none | none | none | none | none |
| `llm.router` + `build_default_router()` | none | none | none | none | none | none |
| `llm.client` | none | none | none | none | none | none |
| `sql_extractor.extractor` | none | **none (loads zero `src.core.llm` modules)** | none | none | none | none |
| `text_to_sql_agent` (façade) | none | none | loads (A2 composition debt) | none | none | (is) |
| `agents.orchestrator` (3.0C.29) | **none** | **none** | loads (documented A2 composition import) | none | none | none |

Layer distinction enforced: lightweight abstraction layer stays SDK-free; the provider implementation layer alone touches SDKs (on construction).

## 11. Import-Time Side Effects

* `TestImportTimeSideEffects` (CPython audit hooks + filesystem diff, fresh subprocess) extended from 4 to **9 protected targets**: the five LLM abstraction modules (`llm.policy/errors/base/router/client`) all pass — no sockets, no process spawns, no file writes, no logging/state configuration at import.
* **Documented carve-out**: `sql_extractor.extractor` is deliberately not an audit-hook target — it calls the sanctioned `get_logger()` at module level, which attaches the application log handler by design (lazy-logging convention, `dependency-rules.md` §4.5). Legitimate static initialization is not classified as a failure, per 3.0C.31. Its LLM/SDK isolation is separately pinned (§10).
* **Provider modules (3.0C.8)**: all four pass dedicated probes that pre-patch SDK constructors (`OpenAI`/`ChatGroq`/`genai.Client`/`OllamaLLM`) and all HTTP surfaces to raise, then import the module — proving no live client construction, no network call, and no credential transmission at import. (`load_dotenv()` env reads are accepted static init.)

## 12. Fake Provider Substitution

`tests/test_architecture/test_provider_substitution.py` (new): in a **fresh subprocess**, a `FakeProvider` behind `BaseLLMClient` is injected into `LLMRouter`, task-bound `LLMClient` adapters feed the real `SQLExtractor` and the real answer seam, and the real `Orchestrator` runs `create_initial_state → run` to successful completion (`state.is_success()`), with the SQL executed through the real `DatabaseConnection`/`QueryExecutorAdapter` against in-memory fakes. The subprocess then reports `sys.modules`: **zero** occurrences of `openai`, `langchain`, `google.genai`, `groq`, `ollama`, `requests`. This proves the application depends on the abstraction, not merely that the abstraction exists.

## 13. Dependency Cycle Validation

* Package cycle test (`test_no_dependency_cycles_among_internal_packages`): **PASS** (acyclic).
* Cycle-prevention rule `test_llm_layer_never_imports_agents_package`: **PASS** — no `src/core/llm/*` module imports `src.agents`; the reverse edge (`contracts → src.core.llm.policy` leaf) is the approved ADR-016 pattern. No unapproved cycle exists.

## 14. F3 Debt Resolution

**Fully resolved.** F3 (per `docs/architecture/package-architecture.md`): the `orchestrator → sql_extractor.extractor → llm.client → openai_client` transitive SDK chain.

* The chain is gone: extractor imports no LLM module; `llm.client` imports no provider; the openai SDK loads only when a provider is actually constructed — proven for the extractor, façade and Orchestrator by fresh-subprocess probes (§10) and pinned permanently (the probes fail if the chain returns).
* Documentation updated: F3 row marked RESOLVED (`package-architecture.md`); the "F1/F2/F3 doors" sentence narrowed to F1/F2; developer-guide debt bullet marked resolved; ADR-011 records the resolution.
* There was no test-level F3 whitelist to delete — the Sprint 2 probes simply did not cover the orchestrator path; that gap is now closed with a targeted probe (no broad whitelist introduced).

## 15. Observability

* Events (shared `log_event` surface, no second telemetry system): `llm_router_started`, `llm_provider_skipped`, `llm_provider_failed`, `llm_router_fallback`, `llm_router_completed` — every chain transition (skip or failure) emits the fallback event with reason.
* Payloads carry provider/model/task/attempt/error-category/duration only; **never** prompts, responses, keys, or credentials (pinned by `test_events_never_contain_prompt_content`).
* **TraceContext propagation proven**: the JSON formatter attaches the active trace/session id to router events (new test formats an emitted `llm_router_started` record inside an active trace context and asserts `trace_id` present).
* Token usage: carried on `LLMResult` (`Usage`, zero until per-provider wiring — documented).

## 16. Health Endpoint

`GET /api/v1/health` returns the unchanged shape `{status, database, llm}`. Availability semantics: configuration/provider probe (`router.has_available_provider()`), no connectivity ping. No-network proof: `test_health_availability_is_configuration_based_without_network` serves a real `LLMClient`+router through the endpoint with all HTTP surfaces (`requests`, `httpx`, `urllib`) patched to raise — passes.

## 17. Compatibility

* **LLMClient**: no-arg constructor, `generate(prompt) -> str`, `is_available()` — all test-pinned; `is_internet_available()` removed with zero repository-wide callers (grep-verified; it was the ping helper — removal covered by the approved ping-removal exception).
* **TextToSQLAgent**: one router at the composition boundary; task-bound `sql_generation`/`response_generation` adapters; `OrchestratorDeps.llm_router` wired; legacy `object.__new__` test-construction path preserved; no global router (pinned).
* **API**: `/api/v1/ask` contract unchanged (integration suite green); `AskRequest`/`AskResponse` untouched.
* **CLI**: evaluation CLI suite green; no redesign.
* **Evaluation**: `EvaluationAgentAdapter` wraps `TextToSQLAgent.ask` — the real path (Agent → Orchestrator → Router) is what evaluation exercises; no separate evaluation-only LLM path exists (grep-verified: the only "openai" string in `evaluation/` is a metrics token check). Offline validation = green `test_evaluation` suite; **Live = NOT RUN** (no live providers available; none fabricated).

## 18. Security Regression

`tests/test_security` (SQL security, database boundary): **PASS** (within the 102-test group run, §25). Security Core untouched, fail-closed behavior intact, no LLM path bypasses validation (SQL executes only through the security-checked execution boundary — exercised end-to-end by the substitution test).
`Security behavior changed: NO`

## 19. Database Regression

`tests/test_database` (safe execution): **PASS** (same group run). Row limits, timeouts, fetch batching, typed DB errors, cursor/connection lifecycle unchanged.
`Database execution behavior changed: NO`

## 20. AgentState Regression

State, boundary, single-writer and isolation suites (`tests/test_agents` + `test_single_writer.py`): **PASS** (within the 651-test architecture+agents run). The router never touches AgentState; the Orchestrator remains the sole writer.
`AgentState ownership changed: NO`

## 21. Documentation Changes

* **Created**: `docs/architecture/llm-developer-guide.md` (practical recipes: using the router, task policies, failure behavior, provider addition, offline testing, leak checklist).
* **Updated**: `docs/architecture/llm-architecture.md` (testing strategy incl. substitution proof + developer-guide cross-ref); `docs/architecture/package-architecture.md` (F3 RESOLVED row; F1/F2 doors sentence); `docs/architecture/developer-package-guide.md` (openai-chain debt bullet marked resolved; orchestrator footprint note); `docs/adr/ADR-011-llm-routing.md` (F3 resolution + cross-refs). (`dependency-rules.md` and `migration-plan.md` were already reconciled in 3.0B; re-verified accurate.)

## 22. Architecture Test Changes

**Added**
* `tests/test_architecture/test_provider_substitution.py` — the 3.0C.9 proof (protects: "application depends on the abstraction"; fails if any provider SDK import enters the app path).
* Orchestrator footprint probe (`test_import_purity.py`) — pins F3 removal + documents the remaining DB-driver composition load; fails if a provider SDK/impl reaches the Orchestrator.
* Provider import side-effect probes ×4 (`test_import_purity.py`) — pin lazy/no-network/no-construction import behavior per provider.
* Signature-parity tests (providers vs `BaseLLMClient.generate`; router vs canonical Protocol) — catch interface drift beyond `runtime_checkable`.
* API-key sanitization test; TraceContext-propagation test.
* `SIDE_EFFECT_TARGETS` extended with the five LLM abstraction modules (import-time side-effect protection).

**Modified**
* `test_import_purity.py`: probe caching (`lru_cache`) to avoid redundant subprocesses (3.0C.34); markers extended (`groq`, `ollama`); probe output enriched (src_modules, pymssql flag).
* `test_llm/test_router.py`: weak protocol-declaration check replaced with a metaclass-based precise check (quality review 3.0C.33 — the old check only inspected one name).

**Removed**: none. No existing test weakened.

## 23. Performance / Test Cost

| Suite | Before 3.0C | After 3.0C |
|---|---|---|
| Full suite | 31.9 s (824 tests) | 77.3 s (849 tests) |
| `tests/test_llm` | 10.0 s (87) | 40.5 s (96) |
| architecture+agents | 17.5 s (635) | 37.9 s (651) |

The increase is the cost of the new subprocess-based enforcement: 5 side-effect probes (new targets, cached), 4 provider side-effect probes (each imports the SDKs to pre-patch constructors — the dominant share), the orchestrator footprint probe and the substitution probe. Redundancy is already minimized by `lru_cache` on shared probes; footprint-vs-side-effect probes remain deliberately separate because they prove different invariants (3.0C.34). Per-provider isolation in the side-effect probes was kept (clearer attribution) at the cost of ~9 s.

## 24. Migration Debt

### Resolved
* F3 transitive openai-SDK chain (fully removed and pinned).
* Orchestrator/extractor footprint probes previously missing — gap closed.
* Import-time side-effect protection previously absent for LLM surfaces — added.

### Remaining
* Task-bound `LLMClient` adapter at the extraction/answer seams (retires when `SQLGenerator`/`AnswerGenerator` land — dependency-rules §11).
* Orchestrator/facade DB-driver import load (`database.connection` composition debt A2 — not provider leakage).
* Token usage zero until per-provider `Usage` wiring.

### Intentionally Deferred
* Legacy `SQLExtractor` correction retry loop (bounded, unchanged — 3.0C.13 defers redesign).
* Legacy `SQLValidator` wrapper (ADR-014); `os.getenv` grandfathering (§4.6).
* Gemini live-API validation; Groq coarse error classification; Ollama override limitations (documented).

## 25. Test Results

| Command | Result | Duration |
|---|---|---|
| `uv run python -m pytest tests -q` | **849 passed, 340 skipped, 0 failed** | 77.3 s |
| `uv run python -m pytest tests -q -rs` | 849 passed, 340 skipped (skip audit §2) | 76.8 s |
| `uv run python -m pytest tests/test_llm -q` | 96 passed | 40.5 s |
| `uv run python -m pytest tests/test_architecture tests/test_agents -q` | 651 passed, 340 skipped | 37.9 s |
| `uv run python -m pytest tests/test_security tests/test_database tests/test_observability tests/test_evaluation tests/test_integration -q` | 102 passed | 1.7 s |
| `uv run python -m compileall -q src evaluation scripts tests` | exit 0, clean | — |
| Formatter/static checks | none configured in the repository (`pyproject.toml` has no lint/format tool) | — |

Arithmetic: 824 (3.0B) + 25 new enforcement tests = 849. No test hidden, skipped ad hoc, or weakened.

## 26. Files Created

* `tests/test_architecture/test_provider_substitution.py`
* `docs/architecture/llm-developer-guide.md`
* `docs/sprints/sprint-3-prompt-3.0c-report.md` (this report)

## 27. Files Modified

* `tests/test_llm/test_import_purity.py` (caching, orchestrator footprint probe, provider side-effect probes, marker/output extensions)
* `tests/test_llm/test_provider_clients.py` (signature parity, API-key sanitization)
* `tests/test_llm/test_router.py` (protocol-declaration check, router signature parity, trace propagation)
* `tests/test_architecture/test_boundary_hardening.py` (SIDE_EFFECT_TARGETS extension + documented carve-out)
* `docs/architecture/llm-architecture.md`, `docs/architecture/package-architecture.md`, `docs/architecture/developer-package-guide.md`, `docs/adr/ADR-011-llm-routing.md`

(All `src/**` modifications on the branch predate 3.0C and belong to Stage 3.0B — this stage changed **no production code**.)

## 28. Deviations From 3.0A/3.0B

* **D1 (documented carve-out)**: `sql_extractor.extractor` excluded from audit-hook side-effect targets. Justification: its module-level `get_logger()` attaches the application log handler by design — the repository's documented lazy-logging convention (`dependency-rules.md` §4.5; the probe's own docstring). 3.0C.31's carve-out for legitimate static initialization applies. Its LLM/SDK isolation remains probe-enforced.
* **D2 (documentation discrepancies in the task's authoritative list)**: `docs/sprints/sprint-3a-preflight.md` does not exist at that path (actual: `docs/implementation/sprint-3a-preflight.md`); `docs/sprints/sprint-3b-report.md` and `docs/implementation/sprint-3a-report.md` do not exist (the 3.0A/3.0B reports were delivered as chat reports; the 3.0A record is the preflight). No content was invented; this report provides the reconciliation. Recommendation recorded (§31) to persist future stage reports under `docs/sprints/`.
* **D3 (task-list files)**: `docs/sprints/sprint-2-prompt-3c-report.md` etc. exist and were consulted; no listed document was missing content that affected implementation.
* No other deviations. No production behavior changed in 3.0C.

## 29. Risks / Known Limitations

* The provider side-effect probes import the real SDKs to pre-patch constructors — they require the SDK packages to be installed (they are project dependencies) and add ~12 s to the suite.
* The orchestrator/facade DB-driver import load is pinned as "expected" in the footprint probe; if the composition debt is later paid off, that assertion must be flipped (documented in the test).
* Gemini remains selectable but unvalidated against the live API; Groq maps all failures to one error class; Ollama ignores timeout/temperature/max_tokens overrides — all documented and tested at their defined behavior.
* Token usage stays zero until per-provider `Usage` wiring (a later, LLM-quality stage).

## 30. Final Status

**PASS WITH DEBT** (remaining debt is pre-existing, documented, and intentional — §24)

* Runtime behavior changed: **NO** (no production code changed in 3.0C)
* Public API compatibility changed: **NO**
* Provider behavior changed: **NO**
* Security behavior changed: **NO**
* Database execution behavior changed: **NO**
* AgentState ownership changed: **NO**
* Observability behavior changed: **NO**

## 31. Recommendation

The approved Sprint 3 / Prompt 3.0 objective — **Application → LLMRouter → provider adapter → external SDK, with the SDK never becoming the application contract** — is complete and mechanically enforced. Recommended next step, upon explicit authorization: **persist the 3.0A/3.0B stage reports under `docs/sprints/`** (small documentation-hygiene task closing deviation D2), after which the next *approved* architectural sprint (e.g. Schema Intelligence per ADR-002) can be planned. Do not start Schema Intelligence or any other sprint without explicit authorization.
