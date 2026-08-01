# src/core/llm/client.py

import os
from src.core.llm.openai_client import OpenAIClient

class LLMClient:
    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 500,
        base_url: str = None
    ):
        if provider == "openai":
            self.client = OpenAIClient(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                base_url=base_url or os.getenv("OPENAI_BASE_URL")
            )
        elif provider == "groq":
            from src.core.llm.groq_client import GroqClient
            self.client = GroqClient(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    def generate(self, prompt: str) -> str:
        return self.client.generate(prompt)