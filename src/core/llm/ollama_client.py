# src/core/llm/ollama_client.py

from langchain_ollama import OllamaLLM
from src.core.llm.base import BaseLLMClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class OllamaClient(BaseLLMClient):
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

    def generate(self, prompt: str) -> str:
        if not self.llm:
            raise RuntimeError("Ollama model not initialized.")
        try:
            return self.llm.invoke(prompt)
        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
            raise

    def is_available(self) -> bool:
        return self.llm is not None