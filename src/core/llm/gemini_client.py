# src/core/llm/gemini_client.py

import os
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai.errors import httpx

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import (
    LLMProviderError,
    LLMProviderRateLimited,
    LLMProviderTimeout,
)

load_dotenv()


class GeminiClient(BaseLLMClient):
    """Google Gemini client via ``google-genai``.

    Implemented and selectable via ``LLMPolicy(provider="gemini")``, but
    NOT part of the default fallback chain (Sprint 3A keeps the approved
    ``openai → groq → ollama`` default; Gemini has never been exercised
    against the live API in this repository — documented in
    ``docs/architecture/llm-architecture.md``).
    """

    name = "gemini"

    def __init__(self, model="gemini-2.5-flash", temperature=0.2, max_tokens=500):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is not set in environment variables.")
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def is_available(self) -> bool:
        """Cheap configuration check — no network calls."""
        return os.getenv("GOOGLE_API_KEY") is not None

    def generate(
        self,
        prompt: str,
        *,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        config = {
            "temperature": self.temperature if temperature is None else temperature,
            "max_output_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if timeout_seconds is not None:
            config["http_options"] = {"timeout": int(timeout_seconds * 1000)}
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            return response.text
        except httpx.TimeoutException as e:
            raise LLMProviderTimeout(
                "Gemini request timed out.",
                provider=self.name, model=self.model, cause=e,
            ) from e
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) == 429:
                raise LLMProviderRateLimited(
                    "Gemini rate limit exceeded.",
                    provider=self.name, model=self.model, cause=e,
                ) from e
            raise LLMProviderError(
                f"Gemini API error: {e}",
                provider=self.name, model=self.model, cause=e,
            ) from e
        except Exception as e:
            raise LLMProviderError(
                f"Gemini API error: {e}",
                provider=self.name, model=self.model, cause=e,
            ) from e
