# LLM Architecture

**Sprint**: 3 / Prompt 3.0B — LLMRouter implementation & legacy provider decoupling
**Status**: Implemented; enforced by `tests/test_llm/` and `tests/test_architecture/test_llm_boundary.py`
**Companion documents**:
- [`docs/adr/ADR-011-llm-routing.md`](../adr/ADR-011-llm-routing.md)
- [`docs/adr/ADR-016-package-architecture.md`](../adr/ADR-016-package-architecture.md)
- [`docs/architecture/dependency-rules.md`](dependency-rules.md) — §3.17, §4.2, §11

---

## 1. Structure

```
Application / Orchestrator / core logic (SQL extraction, answer seam)
        │  generate(prompt) -> str  (legacy duck-typed seam)
        ▼
LLMClient — task-bound compatibility adapter  (src/core/llm/client.py)
        │  generate(prompt, *, task, policy, budget) -> LLMResult
        ▼
LLMRouter — the provider boundary              (src/core/llm/router.py)
        │  BaseLLMClient interface              (src/core/llm/base.py)
        ▼
Provider implementations — the only SDK imports
        (openai_client.py, groq_client.py, gemini_client.py, ollama_client.py)
        ▼
External provider SDKs (openai / langchain_groq / google-genai / langchain_ollama)
```

## 2. Contract Surface (single contract authority, ADR-012)

| Object | Canonical location | Notes |
|---|---|---|
| `LLMRouter` Protocol | `src/agents/contracts.py` | `generate(prompt, *, task, policy=None, budget=None) -> LLMResult`, `runtime_checkable` |
| `LLMRouterError` | `src/agents/contracts.py` | Component reporting error (ADR-015); distinct from the provider-error taxonomy |
| `Usage`, `LLMPolicy`, `LLMBudget`, `LLMResult` | `src/core/llm/policy.py` | Provider-neutral value objects; re-exported by `contracts.py` (ADR-016 leaf pattern — the LLM layer must not import `src.agents`, so the canonical definitions live in a driver-free leaf) |
| Provider error taxonomy | `src/core/llm/errors.py` | `LLMProviderError` (+`Unavailable`/`Timeout`/`RateLimited`), `LLMConfigurationError` (not fallback-eligible) |
| Provider interface | `src/core/llm/base.py` | `BaseLLMClient`: `name`, `model`, `generate(prompt, *, timeout_seconds=None, temperature=None, max_tokens=None) -> str`, `is_available() -> bool` |

`contracts.py` re-exports the four value objects, so
`from src.agents.contracts import LLMResult` keeps working; identity is
pinned by `test_contracts_reexports_canonical_objects`.

## 3. Router Behavior

* **Deterministic selection** — the chain is `openai → groq → ollama` by
  default, overridable via `LLM_PROVIDER_ORDER` and per-call `LLMPolicy`
  (`provider` = preferred first; `fallback_chain` = full override). Same
  inputs always produce the same order; no randomness.
* **Gemini** — registered and explicitly selectable
  (`LLMPolicy(provider="gemini")`) but **not** in the default chain; it
  has never been exercised against the live API in this repository.
* **Lazy registry** — providers are constructed on first use from
  factories; importing or constructing the router loads no SDK.
* **Bounded retry** — `max_attempts_per_provider` (default 1; SDK-internal
  retries unchanged). Unavailable providers are never retried.
* **Classified fallback** — the router advances to the next provider only
  for provider-classified failures (`LLMProviderError` subtypes).
  `LLMConfigurationError` aborts immediately: a request that no provider
  can accept must not burn the chain.
* **Budget** — `LLMBudget.timeout_ms` is mapped to a per-request
  `timeout_seconds`; attempts are bounded by the chain length ×
  `max_attempts_per_provider`. No unbounded loops exist.
* **Result** — canonical `LLMResult` (text, provider, model, `Usage`,
  latency, task/attempt/fallback metadata). Provider SDK response objects
  never cross the boundary.

## 4. Ownership Matrix

| Concern | Owner |
|---|---|
| Model selection | `LLMPolicy.model` / provider default; no app-layer hardcoding |
| Provider configuration | `src/utils/config_loader.py` (`load_llm_routing_config`); provider credentials via each client's env var |
| Retry | Router (`max_attempts_per_provider`), bounded |
| Timeout | `LLMBudget.timeout_ms` → provider per-request timeout; router owns no other timing |
| Provider error mapping | Each provider client (SDK exceptions → neutral taxonomy); router normalizes anything unclassified |
| Fallback decisions | Router only |
| Token usage | Provider SDKs populate what they can; router carries `Usage` on `LLMResult` (zero until wired per-provider) |
| Trace context | Structured logging surface (`log_event`); no second instrumentation path |
| Provider SDK imports | Only `src/core/llm/{openai,groq,gemini,ollama}_client.py` |
| Provider adapter construction | Router factories / explicit injection; never at module import time |

## 5. Configuration

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER_ORDER` | Comma-separated routing chain override (e.g. `groq,openai`). Unknown names fail fast with `ConfigurationError`. |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | OpenAI configuration |
| `GROQ_API_KEY` | Groq configuration |
| `GOOGLE_API_KEY` | Gemini configuration |
| — | Ollama is local; no API key |

Defaults live in `load_llm_routing_config()`; nothing is read from
`os.environ` inside the router itself.

## 6. Observability

Structured events through the existing logging surface — no second
system: `llm_router_started`, `llm_provider_skipped`,
`llm_provider_failed`, `llm_router_fallback`,
`llm_router_completed`. Events carry provider/model/task/attempt/error
category and durations; never raw prompts, responses, keys, or
credentials (pinned by `test_events_never_contain_prompt_content`).

## 7. Compatibility

* **`LLMClient`** — task-bound adapter (`generate(prompt) -> str`,
  `is_available()`); the no-argument constructor keeps
  `scripts/generate_column_map.py` working. `is_internet_available()`
  (the ping helper) was removed with the ping — it had no external
  callers.
* **Connectivity ping — removed intentionally**: the legacy pre-call
  `requests.get("https://api.openai.com")` produced false negatives for
  proxied endpoints and latency on every call (`docs/current-system/llm-audit.md`).
  `/health` availability is now configuration/provider-based; response
  keys are unchanged (`status`, `database`, `llm`), and no network call
  is permitted from the health path (pinned by
  `test_health_availability_is_configuration_based_without_network`).
* **`SQLExtractor`** — accepts any `generate(prompt) -> str` client;
  imports no LLM module (pinned by `TestSqlExtractorIsolation` and a
  fresh-subprocess probe). Existing test fakes keep working.
* **`TextToSQLAgent`** — builds one router, two task-bound adapters
  (`sql_generation` for the extractor, `response_generation` for the
  answer seam) and passes the router as `OrchestratorDeps.llm_router`.

## 8. Testing Strategy

No test requires a live provider. Fakes implement `BaseLLMClient`
(`tests/test_llm/conftest.py`); SDK exception mapping uses constructed
SDK exception objects. Fresh-subprocess probes prove import purity, the
Orchestrator footprint and provider import-time side effects
(`tests/test_llm/test_import_purity.py`); the decisive architecture test
runs the whole Orchestrator pipeline on a `FakeProvider` in a fresh
subprocess and asserts no provider SDK is ever imported
(`tests/test_architecture/test_provider_substitution.py`); static AST
tests enforce the boundary (`tests/test_architecture/test_llm_boundary.py`);
package-level cycle detection is covered by the existing
`test_no_dependency_cycles_among_internal_packages`. Practical recipes:
[`llm-developer-guide.md`](llm-developer-guide.md).

## 9. Adding a Provider

1. Create `src/core/llm/<name>_client.py` subclassing `BaseLLMClient`;
   set `name`, expose `model`, translate SDK exceptions to the neutral
   taxonomy, keep `is_available()` cheap (no network).
2. Register a lazy factory in `_DEFAULT_FACTORIES`
   (`src/core/llm/router.py`) and the credential env var in
   `_PROVIDER_KEY_ENV` (`src/utils/config_loader.py`).
3. Decide its position: default chain (edit `DEFAULT_PROVIDER_ORDER`) or
   explicit-selection-only (like Gemini).
4. Add conformance tests to `tests/test_llm/test_provider_clients.py`
   and the upward-import rule to `test_llm_boundary.py`.

Do not import the new SDK anywhere outside the provider module.
