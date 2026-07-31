# src/core/llm/client.py

import requests
from src.core.llm.base import BaseLLMClient
from src.core.llm.openai_client import OpenAIClient
from src.core.llm.ollama_client import OllamaClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class LLMClient(BaseLLMClient):
    def __init__(self):
        self.openai_client = None
        self.ollama_client = None
        self._init_clients()

    def _init_clients(self):
        try:
            self.openai_client = OpenAIClient()
        except Exception as e:
            logger.warning(f"OpenAI client init failed: {e}")
            self.openai_client = None

        try:
            self.ollama_client = OllamaClient()
        except Exception as e:
            logger.warning(f"Ollama client init failed: {e}")
            self.ollama_client = None

    def is_internet_available(self) -> bool:
        """Check internet connectivity by pinging OpenAI API (or any public endpoint)."""
        try:
            requests.get("https://api.openai.com", timeout=3)
            return True
        except:
            return False

    def generate(self, prompt: str) -> str:
        # Try OpenAI if available and internet is up
        if self.is_internet_available() and self.openai_client:
            try:
                return self.openai_client.generate(prompt)
            except Exception as e:
                logger.warning(f"OpenAI generation failed: {e}. Falling back to Ollama.")
        # Fallback to Ollama if available
        if self.ollama_client and self.ollama_client.is_available():
            try:
                return self.ollama_client.generate(prompt)
            except Exception as e:
                logger.error(f"Ollama generation failed: {e}")
        raise RuntimeError("No LLM client available. Please check your configuration and network.")

    def is_available(self) -> bool:
        return (self.openai_client is not None and self.is_internet_available()) or \
               (self.ollama_client is not None and self.ollama_client.is_available())