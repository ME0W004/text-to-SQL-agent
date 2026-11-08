# src/core/llm/base.py

"""Canonical internal provider interface (Sprint 3A, ADR-011).

Every provider client in ``src/core/llm/`` conforms to this interface and
the ``LLMRouter`` drives providers exclusively through it. This is the
*internal* provider seam — the provider-neutral application-facing
contract is the ``LLMRouter`` Protocol in ``src/agents/contracts.py``
(single contract authority, ADR-012). No competing abstraction exists:
providers implement ``BaseLLMClient``; the router implements the
contracts Protocol.

Provider SDK imports belong in the concrete provider modules only
(enforced whole-tree by ``tests/test_architecture/test_boundary_hardening.py``).

All keyword parameters of ``generate`` are optional overrides; ``None``
means "use the provider's configured default", so a call
``provider.generate(prompt)`` behaves exactly as before Sprint 3A.
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseLLMClient(ABC):
    """Base class for all LLM provider clients.

    Subclasses MUST:

    * set ``name`` to the stable provider identifier used in routing
      policy (``"openai" | "groq" | "gemini" | "ollama"``);
    * expose the configured ``model`` as an attribute;
    * accept the optional ``generate`` keyword overrides (implementations
      may ignore those their SDK does not support — documented per
      provider);
    * keep their SDK imports inside their own module;
    * make ``is_available`` cheap: configuration/state checks only, no
      network calls (the router may consult it between attempts).
    """

    #: Stable routing identifier (class attribute).
    name: str = "base"

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a response from the model given a prompt."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this provider can currently serve requests."""
        ...
