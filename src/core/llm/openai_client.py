# src/core/llm/openai_client.py

import os
from openai import OpenAI
from src.core.llm.base import BaseLLMClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class OpenAIClient(BaseLLMClient):
    def __init__(self, model="gpt-4o-mini", temperature=0.2, max_tokens=500):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set in environment.")
        self.client = OpenAI(api_key=self.api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, prompt: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            raise

    def is_available(self) -> bool:
        try:
            # quick test: list models or just ping
            self.client.models.list()
            return True
        except:
            return False