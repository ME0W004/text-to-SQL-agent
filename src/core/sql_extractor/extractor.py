# src/core/sql_extractor/extractor.py

from src.core.sql_extractor.prompt_builder import PromptBuilder
from src.core.sql_extractor.validator import SQLValidator
from src.core.llm.client import LLMClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SQLExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.validator = SQLValidator()

    def extract(self, question: str, history: list = None) -> str:
        """Convert a natural language question to a SQL query."""
        prompt = PromptBuilder.build_sql_prompt(question, history)
        logger.debug(f"Prompt sent to LLM:\n{prompt}")

        # Generate SQL (may include extra text, we'll clean)
        raw_sql = self.llm.generate(prompt)

        # Clean: remove markdown code blocks if any
        sql = raw_sql.strip()
        if sql.startswith("```sql"):
            sql = sql[6:]
        if sql.endswith("```"):
            sql = sql[:-3]
        sql = sql.strip()

        # Validate
        try:
            self.validator.validate(sql)
            return sql
        except Exception as e:
            logger.warning(f"Validation error: {e}. Attempting to retry or return raw.")
            # In phase 1, we simply return the raw SQL; in phase 4 we'll implement retry loop.
            # For now, we raise to let the agent handle.
            raise