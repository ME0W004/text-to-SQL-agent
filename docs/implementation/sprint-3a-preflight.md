# Sprint 3A — Preflight Report: LLMRouter Implementation and Legacy Provider Decoupling

> Written before any production change. Facts verified against the working tree at
> branch `new-ar`, baseline regression: **720 passed / 340 skipped / 0 failed**,
> `compileall` clean.

---

## 1. Current LLM interfaces

| Surface | Location | Shape | Status |
|---|---|---|---|
| `BaseLLMClient` (ABC) | `src/core/llm/base.py` | `generate(prompt) -> str`, `is_available() -> bool` | Only `OllamaClient` implements it |
| `OpenAIClient` | `src/core/llm/openai_client.py` | `generate(prompt) -> str` via raw `openai` SDK; constructor raises `ValueError` without `OPENAI_API_KEY`; no explicit timeout (SDK default applies) | **Active** — the only wired provider |
| `GroqClient` | `src/core/llm/groq_client.py` | `generate(prompt) -> str` via `langchain_groq.ChatGroq` | Dead code (never constructed) |
| `GeminiClient` | `src/core/llm/gemini_client.py` | `generate(prompt) -> str` via `google.genai` | Dead code (never constructed) |
| `OllamaClient` | `src/core/llm/ollama_client.py` | `generate(prompt) -> str` via `langchain_ollama.OllamaLLM`; lazy `_init_llm` with try/except | Dead code (never constructed) |
| `LLMClient` (facade) | `src/core/llm/client.py` | `generate(prompt) -> str`, `is_available()`, `is_internet_available()` | **Active** — hardwires OpenAI |
| `LLMRouter` (Protocol) | `src/agents/contracts.py:957` | `generate(prompt, *, task, policy, budget) -> LLMResult` | Canonical contract — **no implementation exists yet** |
| `LLMPolicy` / `LLMBudget` / `LLMResult` / `Usage` | `src/agents/contracts.py` | Provider-neutral value objects | Defined; unused at runtime |
| `LLMRouterError` | `src/agents/contracts.py:757` | `TypedComponentError` (code `ROU.INTERNAL`, retryable) | Defined; unused at runtime |

Canonical contract ownership is singular (`src/agents/contracts.py`, ADR-012/016).
This sprint extends the canonical contract additively; no second abstraction is created.

## 2. Current provider import locations

* Raw `openai` SDK: only `src/core/llm/openai_client.py` (enforced whole-tree by
  `tests/test_architecture/test_boundary_hardening.py::test_openai_sdk_only_imported_by_llm_provider_layer`).
* `langchain_groq` → `src/core/llm/groq_client.py`; `google.genai` →
  `src/core/llm/gemini_client.py`; `langchain_ollama` → `src/core/llm/ollama_client.py`.
* Transitive leakage into core logic: `src/core/sql_extractor/extractor.py` imports
  `src.core.llm.client` (type hint only), which imports `OpenAIClient` and `requests`
  — so the SQL extraction path transitively loads the openai SDK and `requests`.
* The provider client *modules* are imported at module top by
  `src/core/llm/client.py` (`OpenAIClient`), so importing the legacy facade loads
  the openai SDK eagerly.

## 3. Current fallback logic

There is none. `LLMClient.generate` has a single branch: ping `https://api.openai.com`
(3 s, bare `except:`), then call OpenAI, else `RuntimeError`. Provider errors are
re-wrapped as `RuntimeError("OpenAI generation failed.")`. This matches
`docs/current-system/llm-audit.md` (README claims fallback that does not exist).

## 4. Current callers (complete list)

| Caller | Uses | Notes |
|---|---|---|
| `src/agents/text_to_sql_agent.py:74` | `self.llm = LLMClient()` | Facade wiring; passes `llm_client` into `OrchestratorDeps` |
| `src/core/sql_extractor/extractor.py` | `self.llm.generate(prompt)` ×2 (generation + correction) | Constructor type-hints `LLMClient`; real contract is duck-typed `generate(prompt) -> str` |
| `src/agents/adapters.py::default_answer_fn` | `llm_client.generate(prompt)` | Answer generation via `OrchestratorDeps.answer_fn` |
| `src/api/routes.py:66` | `agent.llm.is_available()` | `/api/v1/health` reports `Available`/`Unavailable` |
| `scripts/generate_column_map.py:152` | `LLMClient()` then `generate(prompt)` | Offline script, run via subprocess from `column_mapper_updater` |

Call-site fakes in tests implement exactly `generate(self, prompt)` (e.g.
`tests/test_observability/test_integration.py:101`,
`tests/test_agents/test_orchestrator.py`). **Any change to the `generate(prompt)`
call shape at the extractor/answer layer breaks existing tests** — the migration
must keep `generate(prompt) -> str` at those seams.

## 5. Current configuration flow

* Provider keys/models read per-client via `os.getenv` inside provider modules
  (existing code — allowed to continue per dependency-rules.md §4.6).
* `LLMClient._init_client` decides provider availability solely by
  `OPENAI_API_KEY` presence; temperature (0.1) and max_tokens (500) are hardcoded
  in `client.py`.
* No routing config exists (no provider order, no enable flags). ADR-011 does not
  fix an order; `docs/architecture/dependency-rules.md` §3.17 names the four
  provider clients as the router's allowed dependencies.
* New routing configuration will be centralized in `src/utils/config_loader.py`
  (the sanctioned configuration surface), not scattered `os.getenv` in the router.

## 6. Current exception behavior

* Provider SDK exceptions → `RuntimeError(f"... API error: {e}")` inside each client;
  `LLMClient.generate` re-wraps as `RuntimeError("OpenAI generation failed.")`.
* Orchestrator answer path: any exception → `ErrorCategory.LLM` response, code
  `ANS.LLM_UNAVAILABLE` (no `code` attribute on `RuntimeError`), retryable.
* SQL-generation path: exception propagates through `Orchestrator.run` →
  `_handle_unknown_failure` → `ErrorCategory.INTERNAL`, code = `type(exc).__name__`
  (today `RuntimeError`).
* `src/utils/exceptions.py` has a legacy `LLMError(TextToSQLException)` — currently
  **unused** by the LLM layer (verified by grep). It stays untouched.
* `contracts.LLMRouterError` (typed, ADR-015) exists for the orchestrator's error
  model; it is the component-level reporting error, distinct from the
  provider-neutral translation errors the router needs internally.

## 7. Current tests relevant to this sprint

* `tests/test_architecture/test_boundary_hardening.py` — whole-tree SDK boundaries
  (openai only in `src/core/llm/`), import side-effect probes (protected surfaces).
* `tests/test_architecture/test_import_footprint.py` — lightweight surfaces
  (`contracts`, `state`, `policy`) must not load openai/langchain/requests/etc.
* `tests/test_architecture/test_dependencies.py` — orchestrator/adapters/contracts
  must not import provider SDK modules or `pymssql`.
* `tests/test_observability/test_integration.py:101` — `SQLExtractor(FakeLlm())`
  must keep emitting `llm_call_started/completed` with `purpose` and no raw prompt.
* `tests/test_agents/test_orchestrator.py` — LLM failure on answer path →
  `ErrorCategory.LLM`; unexpected extractor exception → `ErrorCategory.INTERNAL`.
* `tests/test_integration/test_sprint1_path.py` — `/health` returns keys
  `{status, database, llm}`; monkeypatched `agent.llm.is_available` still used.
* `tests/test_llm/` and `tests/test_sql_extractor/` are **empty** (only
  `__init__.py`) — the new router/provider tests go there.
* No existing test asserts the *removal* of the connectivity ping; the ping is
  documented as a defect in `docs/current-system/llm-audit.md`.

## 8. Exact legacy coupling to be removed

1. `SQLExtractor` → `src.core.llm.client` import (transitively pulls the openai
   SDK + `requests` into the SQL extraction path).
2. `LLMClient` hardwiring OpenAI (provider selection inside the legacy facade,
   including the pre-call network ping).
3. No bounded, policy-driven fallback anywhere.

## 9. Proposed minimal migration path

1. **Contracts (additive only)** — extend `LLMPolicy` with optional
   `temperature`/`max_tokens` overrides; everything else already exists
   (`LLMRouter` Protocol, `LLMBudget`, `LLMResult`, `Usage`, `LLMRouterError`).
2. **Provider-neutral error taxonomy** — new `src/core/llm/errors.py`:
   `LLMProviderError` (base) with `LLMProviderUnavailable`, `LLMProviderTimeout`,
   `LLMProviderRateLimited`, `LLMConfigurationError`. These translate provider
   SDK exceptions; they do not replace `contracts.LLMRouterError` (reporting)
   or `utils.exceptions.LLMError` (legacy, unused).
3. **Provider normalization** — all four clients conform to the canonical
   internal provider interface (`BaseLLMClient`: `name`, `model`,
   `generate(prompt, *, timeout_seconds=None) -> str`, `is_available() -> bool`),
   by making them subclass `BaseLLMClient` and adding the missing pieces.
   No logic rewrite; SDK imports stay confined to `src/core/llm/`.
4. **Router** — new `src/core/llm/router.py` implementing the canonical
   `LLMRouter` Protocol. Provider registry with lazy factories (SDK modules
   imported only inside factory bodies, so importing the router module loads no
   SDK); deterministic chain (default `openai → groq → ollama`, env-overridable);
   Gemini registered but **not** in the default chain (selectable via
   `LLMPolicy(provider="gemini")`, documented as untested-against-live-API);
   bounded per-provider attempts; fallback only for provider-classified failures;
   provider-neutral error translation; structured `log_event` telemetry
   (`llm_router_started`, `llm_provider_failed`, `llm_router_fallback`,
   `llm_router_completed`).
5. **Compatibility adapter** — `src/core/llm/client.py::LLMClient` becomes a thin
   task-bound adapter over the router (`generate(prompt) -> str`,
   `is_available()`); the pre-call connectivity ping is removed (documented
   defect; `/health` now reports configuration-based availability). The
   no-argument constructor keeps working (`scripts/generate_column_map.py`).
6. **Wiring** — `TextToSQLAgent` builds one `LLMRouter` and two task-bound
   clients (`sql_generation` for the extractor, `response_generation` for the
   answer path); the router is also passed as `OrchestratorDeps.llm_router`.
   `SQLExtractor` drops its `src.core.llm.client` import and accepts any
   `generate(prompt) -> str` client (unchanged call shape → all existing fakes
   keep passing).
7. **Tests** — new `tests/test_llm/` suites (construction, selection, fallback,
   error normalization, budget mapping, import purity, facade compatibility) and
   strengthened architecture tests (extractor must not import `src.core.llm.*`).
8. **Docs** — `docs/architecture/llm-architecture.md` (new), ADR-011
   implementation status, dependency-rules.md §11 debt update, migration plan
   status.

## 10. Files expected to change

| File | Change |
|---|---|
| `src/agents/contracts.py` | Additive: `LLMPolicy.temperature`/`max_tokens` fields |
| `src/core/llm/errors.py` | **New** — provider-neutral error taxonomy |
| `src/core/llm/base.py` | Canonical internal provider interface (name/model/timeout-capable generate) |
| `src/core/llm/openai_client.py` | Subclass `BaseLLMClient`; `name`; optional per-request timeout; error translation happens in router (client keeps raising typed provider errors) |
| `src/core/llm/groq_client.py` | Subclass `BaseLLMClient`; `name`; `is_available` |
| `src/core/llm/gemini_client.py` | Subclass `BaseLLMClient`; `name`; `is_available` |
| `src/core/llm/ollama_client.py` | Align signature with canonical interface |
| `src/core/llm/router.py` | **New** — `LLMRouter` implementation |
| `src/core/llm/client.py` | Legacy facade → task-bound adapter over the router |
| `src/core/sql_extractor/extractor.py` | Drop `src.core.llm.client` import; duck-typed client |
| `src/agents/text_to_sql_agent.py` | Build router; wire task-bound clients; pass `llm_router` to deps |
| `src/utils/config_loader.py` | Add `load_llm_routing_config()` (provider order override) |
| `tests/test_llm/*` | **New** suites |
| `tests/test_architecture/test_boundary_hardening.py` | Strengthen: extractor/core must not import the LLM layer |
| `docs/architecture/llm-architecture.md` | **New** |
| `docs/adr/ADR-011-llm-routing.md`, `docs/architecture/dependency-rules.md`, `docs/migration/migration-plan.md` | Status updates |
| `docs/implementation/sprint-3a-report.md` | **New** (final report) |

Not touched: Streamlit UI, database layer, memory, FastAPI response contracts,
prompts, SQL semantics, orchestrator logic (only the unused `llm_router` dep
becomes wired at the facade).

## 11. Known risks / decisions to record

* **`/health` semantics change (intentional):** availability becomes
  configuration-based (no network ping). The ping is a documented defect
  (`llm-audit.md` §3, §6) and the sprint forbids unnecessary network checks.
  Public response shape is unchanged.
* **Error codes on failure paths:** router raises provider-neutral exceptions;
  the answer path keeps returning code `ANS.LLM_UNAVAILABLE` (they carry no
  `code` attribute); the SQL-generation failure path currently surfaces
  `type(exc).__name__` — `RuntimeError` becomes e.g. `LLMProviderError`. This is
  an internal error-code string, not part of the API schema; documented in the
  final report. Where a test pins `RuntimeError`, behavior is preserved via the
  extractor raising whatever the injected client raises.
* **`max_tokens`/temperature defaults are unchanged** (0.1/500 at the facade);
  per-task overrides become possible via `LLMPolicy` but are not applied by
  default (model/config changes belong to a later sprint).
