# src/core/llm/router.py

"""LLMRouter — the single entry point for all LLM calls (Sprint 3A, ADR-011).

Implements the canonical ``LLMRouter`` Protocol declared in
``src/agents/contracts.py`` (single contract authority, ADR-012). The
router is the architectural boundary between application/core logic and
individual LLM providers:

    Application / core code
        ↓
    LLMRouter (this module)
        ↓
    BaseLLMClient provider contract (src/core/llm/base.py)
        ↓
    Provider implementations (openai/groq/gemini/ollama clients)

Responsibilities:

* **Deterministic routing** — the provider chain is a configuration
  decision (``LLM_PROVIDER_ORDER`` / default ``openai → groq → ollama``,
  see ``load_llm_routing_config``), optionally overridden per call by
  ``LLMPolicy``.
* **Bounded provider retry** — ``max_attempts_per_provider`` (default 1;
  SDK-internal retries are unchanged). Retries apply to execution
  failures only, never to unavailable providers and never to
  configuration errors.
* **Policy-driven fallback** — the router moves to the next provider only
  for provider-classified failures (``LLMProviderError`` subtypes).
  ``LLMConfigurationError`` is an application failure: it aborts
  immediately instead of burning the chain on a request no provider can
  accept.
* **Error normalization** — callers receive provider-neutral errors from
  ``src/core/llm/errors.py``; raw SDK exceptions never escape.
* **Telemetry** — structured ``log_event`` records (provider, model,
  task, attempt, fallback, latency, error category) through the existing
  observability surface; no second logging system, no secrets.

Isolation rules (enforced by architecture tests):

* This module NEVER imports provider SDKs or provider client modules at
  module level — factories import them lazily inside their bodies, so
  importing this module (and constructing a router) loads no SDK at all.
* Providers are instantiated once per router lifetime and reused across
  calls (matching the application lifecycle); no global singleton, no
  module-level mutable state.
"""

from __future__ import annotations

import logging
import threading
from time import perf_counter
from typing import Callable, Mapping, Optional, Sequence

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import (
    LLMConfigurationError,
    LLMProviderError,
    LLMProviderRateLimited,
    LLMProviderTimeout,
    LLMProviderUnavailable,
)
from src.core.llm.policy import LLMBudget, LLMPolicy, LLMResult, Usage
from src.utils.config_loader import load_llm_routing_config
from src.utils.logger import log_event

__all__ = ["LLMRouter", "build_default_router"]


# ---------------------------------------------------------------------------
# Default provider registry (lazy factories — SDK imports stay in the
# provider modules and only run when a configured provider is first used).
# ---------------------------------------------------------------------------


def _openai_factory() -> BaseLLMClient:
    # temperature=0.1 / max_tokens=500 were the legacy facade-enforced
    # defaults (src/core/llm/client.py before Sprint 3A); preserved here.
    from src.core.llm.openai_client import OpenAIClient

    return OpenAIClient(temperature=0.1, max_tokens=500)


def _groq_factory() -> BaseLLMClient:
    from src.core.llm.groq_client import GroqClient

    return GroqClient()


def _gemini_factory() -> BaseLLMClient:
    from src.core.llm.gemini_client import GeminiClient

    return GeminiClient()


def _ollama_factory() -> BaseLLMClient:
    from src.core.llm.ollama_client import OllamaClient

    return OllamaClient()


_DEFAULT_FACTORIES: dict[str, Callable[[], BaseLLMClient]] = {
    "openai": _openai_factory,
    "groq": _groq_factory,
    "gemini": _gemini_factory,
    "ollama": _ollama_factory,
}


def _error_category(exc: BaseException) -> str:
    if isinstance(exc, LLMProviderUnavailable):
        return "provider_unavailable"
    if isinstance(exc, LLMProviderTimeout):
        return "timeout"
    if isinstance(exc, LLMProviderRateLimited):
        return "rate_limited"
    if isinstance(exc, LLMProviderError):
        return "provider_error"
    return type(exc).__name__


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class LLMRouter:
    """Deterministic, policy-driven router over the provider layer.

    Implements the canonical ``LLMRouter`` Protocol:
    ``generate(prompt, *, task, policy=None, budget=None) -> LLMResult``.

    Parameters
    ----------
    providers:
        Optional ready provider instances (dependency injection for
        tests / alternative wiring). When omitted, the default registry
        is used: providers are constructed lazily from their factories,
        only if their environment configuration is present.
    provider_order:
        Explicit routing chain override. Defaults to the configured
        order (``LLM_PROVIDER_ORDER`` or ``openai → groq → ollama``);
        for injected providers, the injection order.
    max_attempts_per_provider:
        Bounded per-provider retry count for execution failures
        (default 1 — no application-level retry beyond the SDKs' own).
    """

    def __init__(
        self,
        providers: Optional[Sequence[BaseLLMClient]] = None,
        *,
        provider_order: Optional[Sequence[str]] = None,
        max_attempts_per_provider: int = 1,
    ) -> None:
        self._max_attempts = max(1, int(max_attempts_per_provider))
        self._lock = threading.Lock()
        self._instances: dict[str, BaseLLMClient] = {}
        self._factories: Mapping[str, Callable[[], BaseLLMClient]] = {}

        if providers is not None:
            for provider in providers:
                self._instances[provider.name] = provider
            self._order = tuple(
                provider_order if provider_order is not None
                else [provider.name for provider in providers]
            )
        else:
            self._factories = dict(_DEFAULT_FACTORIES)
            self._order = tuple(
                provider_order if provider_order is not None
                else load_llm_routing_config().provider_order
            )

        known = set(self._factories) | set(self._instances)
        unknown = [name for name in self._order if name not in known]
        if unknown:
            raise LLMConfigurationError(
                "Unknown provider(s) in routing order: "
                f"{', '.join(unknown)}. Known: {', '.join(sorted(known))}."
            )

    # ------------------------------------------------------------------
    # Public API (canonical LLMRouter Protocol)
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        task: str,
        policy: Optional[LLMPolicy] = None,
        budget: Optional[LLMBudget] = None,
    ) -> LLMResult:
        """Generate a completion for *prompt*, routed per policy.

        ``task`` is the logical purpose of the call (e.g.
        ``"sql_generation"``, ``"response_generation"``) and flows into
        telemetry and result metadata.
        """
        if not isinstance(task, str) or not task:
            raise LLMConfigurationError(
                "LLMRouter.generate requires a non-empty logical 'task'."
            )

        chain = self._resolve_chain(policy)
        started = perf_counter()
        log_event(
            "llm_router_started",
            component="llm",
            task=task,
            provider_chain=list(chain),
        )

        timeout_seconds = (
            budget.timeout_ms / 1000.0
            if budget is not None and budget.timeout_ms
            else None
        )

        last_error: Optional[Exception] = None
        fallback_from: Optional[str] = None

        for position, name in enumerate(chain):
            # --- selection: skip providers that cannot be brought up ---
            try:
                provider = self._get_provider(name)
            except LLMProviderUnavailable as exc:
                last_error = exc
                log_event(
                    "llm_provider_skipped",
                    level=logging.WARNING,
                    component="llm",
                    provider=name,
                    task=task,
                    reason="unavailable",
                    error_category=_error_category(exc),
                )
                fallback_from = self._transition(
                    chain, position, name, task, "unavailable", exc, fallback_from
                )
                continue
            if not provider.is_available():
                last_error = LLMProviderUnavailable(
                    f"Provider {name!r} is not available.",
                    provider=name,
                    model=getattr(provider, "model", None),
                )
                log_event(
                    "llm_provider_skipped",
                    level=logging.WARNING,
                    component="llm",
                    provider=name,
                    task=task,
                    reason="not_available",
                    error_category="provider_unavailable",
                )
                fallback_from = self._transition(
                    chain, position, name, task, "not_available", last_error, fallback_from
                )
                continue

            # --- bounded execution attempts on this provider ---
            for attempt in range(1, self._max_attempts + 1):
                try:
                    result = self._invoke(
                        provider,
                        prompt,
                        task=task,
                        policy=policy,
                        timeout_seconds=timeout_seconds,
                        attempt=attempt,
                        fallback_from=fallback_from,
                    )
                except LLMConfigurationError:
                    # Application/configuration failure — never retried,
                    # never re-routed to another provider.
                    raise
                except LLMProviderUnavailable as exc:
                    last_error = exc
                    log_event(
                        "llm_provider_failed",
                        level=logging.WARNING,
                        component="llm",
                        provider=name,
                        model=getattr(provider, "model", None),
                        task=task,
                        attempt=attempt,
                        error_category="provider_unavailable",
                    )
                    break  # retrying a dead provider is pointless — fall back
                except LLMProviderError as exc:
                    last_error = exc
                    log_event(
                        "llm_provider_failed",
                        level=logging.WARNING,
                        component="llm",
                        provider=name,
                        model=getattr(provider, "model", None),
                        task=task,
                        attempt=attempt,
                        error_category=_error_category(exc),
                    )
                    if attempt < self._max_attempts:
                        continue  # bounded provider retry
                    break
                log_event(
                    "llm_router_completed",
                    component="llm",
                    provider=result.provider,
                    model=result.model,
                    task=task,
                    attempt=attempt,
                    fallback_used=fallback_from is not None,
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                )
                return result

            # --- fall back to the next provider ---
            fallback_from = self._transition(
                chain, position, name, task, "execution_failed", last_error, fallback_from
            )

        raise last_error if last_error is not None else LLMProviderUnavailable(
            "No LLM provider is available."
        )

    # ------------------------------------------------------------------
    # Availability (used by the /health surface via the LLMClient adapter)
    # ------------------------------------------------------------------

    def has_available_provider(self) -> bool:
        """Whether at least one provider in the chain can serve requests.

        Cheap: configuration checks only; no network calls.
        """
        for name in self._order:
            try:
                provider = self._get_provider(name)
            except LLMProviderUnavailable:
                continue
            if provider.is_available():
                return True
        return False

    @property
    def provider_order(self) -> tuple[str, ...]:
        """The configured routing chain (read-only view for diagnostics)."""
        return self._order

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_chain(self, policy: Optional[LLMPolicy]) -> tuple[str, ...]:
        """Deterministic provider chain for one call."""
        if policy is not None and policy.fallback_chain:
            chain = list(policy.fallback_chain)
            if policy.provider:
                chain.insert(0, policy.provider)
        elif policy is not None and policy.provider:
            chain = [policy.provider] + [n for n in self._order if n != policy.provider]
        else:
            chain = list(self._order)

        known = set(self._factories) | set(self._instances)
        unknown = [name for name in chain if name not in known]
        if unknown:
            raise LLMConfigurationError(
                f"Unknown provider(s) in routing policy: {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(known))}."
            )

        deduped: list[str] = []
        for name in chain:
            if name not in deduped:
                deduped.append(name)
        return tuple(deduped)

    def _transition(
        self,
        chain: tuple[str, ...],
        position: int,
        name: str,
        task: str,
        reason: str,
        last_error: Optional[Exception],
        fallback_from: Optional[str],
    ) -> Optional[str]:
        """Record moving past *name* to the next chain provider.

        Emitted for every chain transition — selection skips and
        post-attempt failures alike — so the fallback trail is complete.
        Returns the updated ``fallback_from`` marker (``None`` at the end
        of the chain, where there is nothing to fall back to).
        """
        if position + 1 >= len(chain):
            return fallback_from
        log_event(
            "llm_router_fallback",
            level=logging.WARNING,
            component="llm",
            provider=name,
            next_provider=chain[position + 1],
            task=task,
            reason=reason,
            error_category=_error_category(last_error) if last_error else None,
        )
        return name

    def _get_provider(self, name: str) -> BaseLLMClient:
        """Return the live provider instance, constructing it lazily.

        Construction failures and missing configuration raise
        :class:`LLMProviderUnavailable` — a *selection* failure, distinct
        from an execution failure.
        """
        instance = self._instances.get(name)
        if instance is not None:
            return instance
        factory = self._factories.get(name)
        if factory is None:
            raise LLMProviderUnavailable(
                f"Provider {name!r} is not registered.", provider=name
            )
        with self._lock:
            instance = self._instances.get(name)
            if instance is None:
                if name not in load_llm_routing_config().configured_providers:
                    raise LLMProviderUnavailable(
                        f"Provider {name!r} is not configured "
                        "(missing environment credentials).",
                        provider=name,
                    )
                try:
                    instance = factory()
                except Exception as exc:
                    raise LLMProviderUnavailable(
                        f"Provider {name!r} failed to initialize: {exc}",
                        provider=name,
                        cause=exc,
                    ) from exc
                self._instances[name] = instance
            return instance

    def _invoke(
        self,
        provider: BaseLLMClient,
        prompt: str,
        *,
        task: str,
        policy: Optional[LLMPolicy],
        timeout_seconds: Optional[float],
        attempt: int,
        fallback_from: Optional[str],
    ) -> LLMResult:
        """One provider attempt; normalizes unknown provider exceptions."""
        call_started = perf_counter()
        try:
            text = provider.generate(
                prompt,
                timeout_seconds=timeout_seconds,
                temperature=policy.temperature if policy is not None else None,
                max_tokens=policy.max_tokens if policy is not None else None,
            )
        except LLMProviderError:
            raise
        except LLMConfigurationError:
            # Configuration failures are application errors, not provider
            # failures — normalizing them here would make the router treat
            # them as fallback-eligible (they are not).
            raise
        except Exception as exc:
            raise LLMProviderError(
                f"Provider {provider.name!r} failed: {exc}",
                provider=provider.name,
                model=getattr(provider, "model", None),
                cause=exc,
            ) from exc
        latency_ms = (perf_counter() - call_started) * 1000.0
        return LLMResult(
            text=text,
            provider=provider.name,
            model=getattr(provider, "model", ""),
            usage=Usage(),
            latency_ms=round(latency_ms, 3),
            metadata={
                "task": task,
                "attempt": attempt,
                "fallback_used": fallback_from is not None,
                "fallback_from": fallback_from,
            },
        )


def build_default_router() -> LLMRouter:
    """Build the router from project configuration (facade wiring helper)."""
    return LLMRouter()
