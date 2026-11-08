# src/core/llm/policy.py

"""Provider-neutral LLM value objects (Sprint 3B, ADR-011 + ADR-016).

Canonical home of the provider-neutral LLM value objects shared by the
application contracts (``src/agents/contracts.py``, which re-exports
them) and the LLM provider layer (``src/core/llm/router.py``).

This module is the LLM analogue of ``src/core/database/policy.py``
(ADR-016 decision 3): a pure, dependency-free leaf that the provider
layer may import without pulling in the application package. The
``LLMRouter`` Protocol and ``LLMRouterError`` remain in
``src/agents/contracts.py`` — the single application-facing contract
authority (ADR-012); this module holds only the value objects the
router itself constructs and consumes.

This module is deliberately dependency-free (stdlib only). It must stay
safe to import from any layer without loading provider SDKs (enforced
by the import-footprint tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

__all__ = ["Usage", "LLMPolicy", "LLMBudget", "LLMResult"]


@dataclass(frozen=True)
class Usage:
    """LLM token usage metadata."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


@dataclass(frozen=True)
class LLMPolicy:
    """Provider selection policy for LLMRouter.

    ``provider`` names the preferred provider; ``fallback_chain`` overrides
    the configured default order when non-empty. ``temperature`` and
    ``max_tokens`` are optional per-call overrides — when ``None`` the
    provider's configured default applies.
    """

    provider: Optional[str] = None  # "openai" | "gemini" | "groq" | "ollama"
    model: Optional[str] = None
    fallback_chain: tuple[str, ...] = ()
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@dataclass(frozen=True)
class LLMBudget:
    """Resource budget for an LLM call."""

    max_tokens: Optional[int] = None
    timeout_ms: Optional[int] = None
    max_latency_ms: Optional[int] = None


@dataclass(frozen=True)
class LLMResult:
    """Result of an LLMRouter call."""

    text: str
    provider: str
    model: str
    usage: Usage
    latency_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)
