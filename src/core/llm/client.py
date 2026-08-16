# src/core/llm/client.py

import os
import requests
from src.core.llm.openai_client import OpenAIClient
from src.core.llm.groq_client import GroqClient
from src.core.llm.ollama_client import OllamaClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class LLMClient:
    def __init__(self):
        self.openai_client = None
        self.groq_client = None
        self.ollama_client = None
        self._init_clients()

    def _init_clients(self):
        """Initialize all available LLM clients."""
        if os.getenv("OPENAI_API_KEY"):
            try:
                self.openai_client = OpenAIClient(
                    temperature=0.1,
                    max_tokens=500
                )
            except Exception as e:
                logger.warning(f"OpenAI client init failed: {e}")

        if os.getenv("GROQ_API_KEY"):
            try:
                self.groq_client = GroqClient(
                    temperature=0.1,
                    max_tokens=500
                )
            except Exception as e:
                logger.warning(f"Groq client init failed: {e}")

        try:
            self.ollama_client = OllamaClient(
                temperature=0.1,
                max_tokens=500
            )
        except Exception as e:
            logger.warning(f"Ollama client init failed: {e}")

    def is_internet_available(self) -> bool:
        """Check internet connectivity by pinging OpenAI API."""
        try:
            requests.get("https://api.openai.com", timeout=3)
            return True
        except:
            return False

    def generate(self, prompt: str) -> str:
        """Generate response using available LLM with fallback."""
        # Try OpenAI first if available and internet is up
        if self.is_internet_available() and self.openai_client:
            try:
                return self.openai_client.generate(prompt)
            except Exception as e:
                logger.warning(f"OpenAI generation failed: {e}. Falling back to Groq or Ollama.")

        # Try Groq (if available)
        if self.groq_client:
            try:
                return self.groq_client.generate(prompt)
            except Exception as e:
                logger.warning(f"Groq generation failed: {e}. Falling back to Ollama.")

        # Fallback to Ollama (local)
        if self.ollama_client and self.ollama_client.is_available():
            try:
                return self.ollama_client.generate(prompt)
            except Exception as e:
                logger.error(f"Ollama generation failed: {e}")

        raise RuntimeError("No LLM client available. Please check your configuration and network.")

    def is_available(self) -> bool:
        """Check if at least one LLM client is available."""
        return (self.openai_client is not None and self.is_internet_available()) or \
               (self.groq_client is not None) or \
               (self.ollama_client is not None and self.ollama_client.is_available())