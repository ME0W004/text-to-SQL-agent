# src/core/llm/errors.py

"""Provider-neutral LLM error taxonomy (Sprint 3A, ADR-011).

The provider layer translates SDK-specific exceptions into these canonical
categories; the ``LLMRouter`` (``src/core/llm/router.py``) makes its
retry/fallback decisions purely on this classification. Application code
never sees raw provider SDK exceptions.

Categories and the router's policy for each:

* ``LLMConfigurationError``  — the *request/routing configuration* is wrong
  (unknown provider name, missing logical task). Never retried and never
  retried on another provider: the same request cannot succeed elsewhere.
  This is an application failure, not a provider failure.
* ``LLMProviderUnavailable`` — the provider cannot serve *before* a call
  succeeds (missing API key, provider disabled, not installed, local server
  down, authentication rejected). Router falls back to the next provider;
  the failed provider itself is not retried.
* ``LLMProviderTimeout``     — the provider call exceeded its deadline.
  Router may retry the provider (bounded) and falls back.
* ``LLMProviderRateLimited`` — quota/rate limit (HTTP 429). Router may retry
  (bounded) and falls back.
* ``LLMProviderError``       — any other provider execution failure (HTTP/API
  error, model unavailable, malformed provider response). Router may retry
  (bounded) and falls back.

Invalid SQL, semantic validation failures and similar application/query
errors never pass through this module: the router only classifies what the
provider layer raises.

This module is deliberately dependency-free (stdlib only): it must stay
safe to import from any layer without loading provider SDKs.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "LLMProviderError",
    "LLMProviderUnavailable",
    "LLMProviderTimeout",
    "LLMProviderRateLimited",
    "LLMConfigurationError",
]


class LLMProviderError(Exception):
    """Base class for provider-side failures (fallback-eligible).

    Carries the routing context so callers and logs can attribute the
    failure without parsing the message. ``cause`` keeps the original
    provider exception for diagnostics; it is never serialized to users.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.cause = cause


class LLMProviderUnavailable(LLMProviderError):
    """Provider cannot serve requests (missing key, down, disabled, auth)."""


class LLMProviderTimeout(LLMProviderError):
    """Provider call exceeded its deadline."""


class LLMProviderRateLimited(LLMProviderError):
    """Provider rejected the call due to quota/rate limiting."""


class LLMConfigurationError(Exception):
    """Routing/request configuration error (application failure).

    Distinct from :class:`LLMProviderError`: no retry and no provider
    fallback — the router raises immediately so callers can fix the
    configuration instead of burning the whole provider chain on a request
    no provider can accept.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
