# src/core/llm/client.py

"""Task-bound compatibility adapter over the LLMRouter (Sprint 3A, ADR-011).

``LLMClient`` used to be the provider-coupled legacy facade: it hardwired
the OpenAI provider, pinged ``https://api.openai.com`` before every
generation (a documented false-negative source for proxied endpoints)
and had no fallback. Since Sprint 3A it is a thin adapter that delegates
every call to the canonical :class:`~src.core.llm.router.LLMRouter` under
a fixed logical ``task``.

Why this wrapper is retained: the established call shape —
``generate(prompt) -> str`` — is relied upon by the SQL-extraction,
answer-generation and column-map seams (including their test fakes).
It will be retired when SQLGenerator/AnswerGenerator land (documented
migration debt, ``docs/architecture/dependency-rules.md`` §11).

Provider selection, retry, fallback, timeouts and error normalization
happen in the router layer — never here.
"""

from __future__ import annotations

from typing import Optional

from src.core.llm.router import LLMRouter, build_default_router

__all__ = ["LLMClient"]


class LLMClient:
    """Compatibility adapter: ``generate(prompt) -> str`` over the router.

    Parameters
    ----------
    router:
        The router to delegate to. When omitted, a default router is
        built from project configuration (``build_default_router``).
    task:
        Logical purpose recorded in telemetry and result metadata
        (``"sql_generation"``, ``"response_generation"``, ...).
    """

    def __init__(self, router: Optional[LLMRouter] = None, *, task: str = "general"):
        self.router = router if router is not None else build_default_router()
        self.task = task

    def generate(self, prompt: str) -> str:
        """Route the prompt through the router; return the text output."""
        result = self.router.generate(prompt, task=self.task)
        return result.text

    def is_available(self) -> bool:
        """Whether the router has a provider that can serve requests.

        Configuration-based (cheap); the legacy pre-call connectivity
        ping was removed — it added latency to every call and produced
        false negatives for proxied OpenAI-compatible endpoints
        (``docs/current-system/llm-audit.md`` §3).
        """
        return self.router.has_available_provider()
