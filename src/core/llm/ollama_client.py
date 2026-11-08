# src/core/llm/ollama_client.py

from typing import Optional

from langchain_ollama import OllamaLLM

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import LLMProviderError, LLMProviderUnavailable
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OllamaClient(BaseLLMClient):
    """Local Ollama client via ``langchain_ollama.OllamaLLM``.

    Availability is decided at construction time (the local server must
    be reachable for the underlying client to come up); ``timeout_seconds``
    and the per-call overrides are not supported by the LangChain wrapper
    and are ignored (documented in
    ``docs/architecture/llm-architecture.md``).
    """

    name = "ollama"

    def __init__(self, model="llama3.1:8b", temperature=0.2):
        self.model = model
        self.temperature = temperature
        self.llm = None
        self._init_llm()

    def _init_llm(self):
        try:
            self.llm = OllamaLLM(model=self.model, temperature=self.temperature)
        except Exception as e:
            logger.error(f"Failed to initialize Ollama: {e}")
            self.llm = None

    def is_available(self) -> bool:
        return self.llm is not None

    def generate(
        self,
        prompt: str,
        *,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        if not self.llm:
            raise LLMProviderUnavailable(
                "Ollama model not initialized.",
                provider=self.name, model=self.model,
            )
        try:
            return self.llm.invoke(prompt)
        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
            raise LLMProviderError(
                f"Ollama generation error: {e}",
                provider=self.name, model=self.model, cause=e,
            ) from e
