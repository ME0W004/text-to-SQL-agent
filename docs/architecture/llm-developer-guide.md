# LLM Developer Guide

**Sprint**: 3 / Prompt 3.0C — practical recipes for the enforced LLM boundary.
The *what and why* lives in [`llm-architecture.md`](llm-architecture.md); this
document is the *how*. The boundary rules summarized here are machine-enforced —
violating them fails the test suite.

---

## Where provider SDK imports belong

```
src/core/llm/<name>_client.py   ← the ONLY place a provider SDK may be imported
src/core/llm/{base,errors,policy}.py   ← provider-neutral (AST-enforced)
src/core/llm/router.py                 ← no eager provider imports (lazy factories only)
src/core/sql_extractor/extractor.py    ← no LLM imports at all
src/agents/*                           ← contracts and façade wiring only
```

Enforcement: `tests/test_architecture/test_llm_boundary.py` (static AST),
`tests/test_architecture/test_boundary_hardening.py` (whole-tree SDK scan +
import side effects), `tests/test_llm/test_import_purity.py` (fresh-subprocess
probes).

## How to use LLMRouter (application code)

Application code never touches providers. Two seams exist:

1. **Router-native (preferred for future components):**

```python
from src.core.llm.policy import LLMBudget, LLMPolicy

result = router.generate(
    prompt,
    task="sql_generation",                      # logical purpose — required
    policy=LLMPolicy(provider="openai", temperature=0.1, max_tokens=500),
    budget=LLMBudget(timeout_ms=5000),
)
text = result.text                              # provider-neutral LLMResult
```

2. **Legacy task-bound adapter (current extraction/answer seams):**

```python
client = LLMClient(router, task="response_generation")
text = client.generate(prompt)                  # generate(prompt) -> str
```

Never construct a provider client in application code; never add a second
adapter layer. Provider errors arrive as `src.core.llm.errors` types —
catch `LLMProviderError`, never an SDK exception.

## How to define task policies

* Default chain and credentials come from `load_llm_routing_config()`
  (`src/utils/config_loader.py`); override the chain per deployment with
  `LLM_PROVIDER_ORDER` (comma-separated, fail-fast on unknown names).
* Per-call: `LLMPolicy(provider=...)` pins a preferred provider;
  `LLMPolicy(fallback_chain=...)` replaces the chain for that call;
  `temperature`/`max_tokens` override the provider defaults where supported.
* `LLMBudget(timeout_ms=...)` becomes the per-request timeout. Attempt count
  is bounded by the chain length × `max_attempts_per_provider` (default 1).

## How provider failures behave

| Failure | Router behavior |
|---|---|
| `LLMProviderUnavailable` | skip provider → next in chain (no retry of the dead provider) |
| `LLMProviderTimeout` / `RateLimited` / generic `LLMProviderError` | bounded retry, then fallback |
| `LLMConfigurationError` | **abort immediately** — never retried, never re-routed |
| chain exhausted | last provider-neutral error raised; raw SDK exceptions never escape |

Do not catch SDK exception types above the provider layer, and do not make
configuration errors fallback-eligible.

## How to add a provider

See [`llm-architecture.md` §9](llm-architecture.md#9-adding-a-provider) —
subclass `BaseLLMClient`, register a lazy factory in
`src/core/llm/router.py::_DEFAULT_FACTORIES`, add the credential env var to
`_PROVIDER_KEY_ENV` in `src/utils/config_loader.py`, decide default-chain vs
explicit-selection-only, then extend the conformance tests. Import the SDK
nowhere else.

## How to test without a live provider

* Reuse `FakeProvider` in `tests/test_llm/conftest.py` (scripted responses and
  failures) — no network, fully deterministic.
* Constructor-SDK-exception mapping tests use real SDK exception objects with
  the SDK call patched (see `tests/test_llm/test_provider_clients.py`).
* Import purity / side effects / provider substitution are fresh-subprocess
  probes (`tests/test_llm/test_import_purity.py`,
  `tests/test_architecture/test_provider_substitution.py`) — the subprocess
  is mandatory because the pytest process itself imports SDKs for unit tests.
* No test in this repository may require a live OpenAI/Groq/Gemini/Ollama
  service or external network access.

## Avoiding provider-type leaks — checklist

* Return/accept only `LLMResult`, `LLMPolicy`, `LLMBudget`, `Usage` and the
  `src/core/llm/errors.py` taxonomy above the provider layer.
* Do not import the value objects from `src.agents.contracts` inside
  `src/core/llm/*` — import `src.core.llm.policy` (the canonical leaf); the
  LLM layer must never import `src.agents` (cycle rule, AST-enforced).
* Canonical ownership: the four value objects are defined once in
  `src/core/llm/policy.py`; `contracts.py` re-exports them. Duplicate
  definitions fail `test_definitions_live_only_in_policy_leaf`.
* Log through `log_event` only; events must not contain prompts, responses,
  keys, or credentials (pinned by telemetry-hygiene tests).
