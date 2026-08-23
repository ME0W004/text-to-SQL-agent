# src/core/llm/base.py

from abc import ABC, abstractmethod

class BaseLLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generate a response from the model given a prompt."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the model is reachable and ready."""
        pass