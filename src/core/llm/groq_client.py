# src/core/llm/groq_client.py

import os
from typing import Optional

from dotenv import load_dotenv
from langchain_groq import ChatGroq

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import LLMProviderError

load_dotenv()


class GroqClient(BaseLLMClient):
    """Groq chat client via ``langchain_groq.ChatGroq``.

    LangChain does not expose a stable per-exception timeout/rate-limit
    taxonomy, so every failure is normalized to the fallback-eligible
    :class:`LLMProviderError` (coarser than OpenAI, documented in
    ``docs/architecture/llm-architecture.md``).
    """

    name = "groq"

    def __init__(
        self,
        model: str = "llama-3.3-70b-versatile",
        temperature: float = 0.2,
        max_tokens: int = 500,
        max_retries: int = 2
    ):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY is not set in environment variables.")

        self.llm = ChatGroq(
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def is_available(self) -> bool:
        """Cheap configuration check — no network calls."""
        return os.getenv("GROQ_API_KEY") is not None

    def generate(
        self,
        prompt: str,
        *,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        # Per-call overrides map onto LangChain's bound-kwargs; the
        # constructor values remain the defaults.
        overrides = {}
        if temperature is not None:
            overrides["temperature"] = temperature
        if max_tokens is not None:
            overrides["max_tokens"] = max_tokens
        try:
            runnable = self.llm.bind(**overrides) if overrides else self.llm
            response = runnable.invoke(prompt)
            return response.content
        except Exception as e:
            raise LLMProviderError(
                f"Groq API error: {e}",
                provider=self.name, model=self.model, cause=e,
            ) from e
