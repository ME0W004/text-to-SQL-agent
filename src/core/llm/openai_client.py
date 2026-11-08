# src/core/llm/openai_client.py

import os
from typing import Optional

from openai import (
    OpenAI,
    APITimeoutError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from dotenv import load_dotenv

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import (
    LLMConfigurationError,
    LLMProviderError,
    LLMProviderRateLimited,
    LLMProviderTimeout,
    LLMProviderUnavailable,
)

load_dotenv()


class OpenAIClient(BaseLLMClient):
    """OpenAI-compatible chat client (raw ``openai`` SDK).

    Works with any OpenAI-compatible endpoint via ``base_url``
    (``OPENAI_BASE_URL``). SDK exception classification happens here —
    the router only ever sees the provider-neutral errors from
    ``src.core.llm.errors``.
    """

    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 500,
        base_url: str = None,
        api_key: str = None,
        timeout: float = None,
    ):
        # اگر base_url داده نشده، از متغیر محیطی یا پیش‌فرض OpenAI استفاده کن
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not set in environment variables.")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def is_available(self) -> bool:
        """Cheap configuration check — no network calls."""
        return bool(self.api_key)

    def generate(
        self,
        prompt: str,
        *,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=self.max_tokens if max_tokens is None else max_tokens,
                timeout=timeout_seconds,
            )
            return response.choices[0].message.content
        except APITimeoutError as e:
            raise LLMProviderTimeout(
                "OpenAI request timed out.",
                provider=self.name, model=self.model, cause=e,
            ) from e
        except RateLimitError as e:
            raise LLMProviderRateLimited(
                "OpenAI rate limit exceeded.",
                provider=self.name, model=self.model, cause=e,
            ) from e
        except AuthenticationError as e:
            raise LLMProviderUnavailable(
                "OpenAI rejected the API key.",
                provider=self.name, model=self.model, cause=e,
            ) from e
        except OpenAIError as e:
            raise LLMProviderError(
                f"OpenAI API error: {e}",
                provider=self.name, model=self.model, cause=e,
            ) from e
        except LLMConfigurationError:
            raise
        except Exception as e:
            # Non-SDK failures (local proxy quirks, response parsing, ...)
            # are normalized so the router never sees raw provider objects.
            raise LLMProviderError(
                f"OpenAI API error: {e}",
                provider=self.name, model=self.model, cause=e,
            ) from e
