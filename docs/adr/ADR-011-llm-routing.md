# ADR-011: LLM Provider Routing

Status: Accepted; **implemented in Sprint 3 / Prompt 3.0B**. LLMRouter abstracts providers, task-specific models, retries, budgets and fallback while keeping domain components provider-neutral.

## Implementation

* Canonical contract: `LLMRouter` Protocol, `LLMRouterError` in
  `src/agents/contracts.py`; value objects (`Usage`, `LLMPolicy`,
  `LLMBudget`, `LLMResult`) canonically in `src/core/llm/policy.py`
  (driver-free leaf, ADR-016 pattern) and re-exported by
  `contracts.py`. The leaf exists so the provider layer never imports
  `src.agents` — the application-facing contract authority is unchanged.
* Router: `src/core/llm/router.py` — deterministic chain
  (`openai → groq → ollama`, env-overridable via `LLM_PROVIDER_ORDER`),
  lazy provider factories, bounded per-provider attempts, fallback only
  for classified provider failures, provider-neutral error translation,
  structured telemetry.
* Provider boundary: SDK imports only in
  `src/core/llm/{openai,groq,gemini,ollama}_client.py`
  (`tests/test_architecture/test_boundary_hardening.py`); provider
  neutrality of `base.py`/`errors.py`/`policy.py` and the no-cycle rule
  (`src/core/llm` never imports `src.agents`) enforced by
  `tests/test_architecture/test_llm_boundary.py`.
* Compatibility: `LLMClient` is a task-bound adapter over the router;
  the pre-call connectivity ping was removed (documented defect,
  approved exception); `/health` availability is configuration-based
  with unchanged response shape.
* F3 debt (Sprint 2 finding: the orchestrator → extractor → openai
  transitive SDK chain) is **fully resolved**: the extractor imports no
  LLM module and the SDK loads only on provider construction — pinned
  by fresh-subprocess probes for the extractor, the façade and the
  Orchestrator (`tests/test_llm/test_import_purity.py`).
* Details: [`docs/architecture/llm-architecture.md`](../architecture/llm-architecture.md),
  practical recipes in [`docs/architecture/llm-developer-guide.md`](../architecture/llm-developer-guide.md).
