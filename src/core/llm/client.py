# src/core/llm/client.py

import os
import requests
import logging
from src.core.llm.openai_client import OpenAIClient
from src.utils.logger import get_logger, log_event

logger = get_logger(__name__)

class LLMClient:
    def __init__(self):
        self.openai_client = None
        self._init_client()

    def _init_client(self):
        """Initialize the OpenAI client if API key is available."""
        if os.getenv("OPENAI_API_KEY"):
            try:
                self.openai_client = OpenAIClient(
                    temperature=0.1,
                    max_tokens=500
                )
            except Exception as e:
                log_event("llm_client_init_failed", level=logging.WARNING, component="llm",
                          provider="openai", error_type=type(e).__name__)

    def is_internet_available(self) -> bool:
        """Check internet connectivity by pinging OpenAI API."""
        try:
            requests.get("https://api.openai.com", timeout=3)
            return True
        except:
            return False

    def generate(self, prompt: str) -> str:
        """Generate response using OpenAI client."""
        if self.is_internet_available() and self.openai_client:
            try:
                return self.openai_client.generate(prompt)
            except Exception as e:
                log_event("llm_provider_failed", level=logging.ERROR, component="llm",
                          provider="openai", model=getattr(self.openai_client, "model", None),
                          error_type=type(e).__name__)
                raise RuntimeError("OpenAI generation failed.") from e
        else:
            raise RuntimeError("OpenAI client is not available or internet is down.")

    def is_available(self) -> bool:
        """Check if the OpenAI client is available and internet is reachable."""
        return self.openai_client is not None and self.is_internet_available()
