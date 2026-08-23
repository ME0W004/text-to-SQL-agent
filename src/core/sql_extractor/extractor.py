# src/core/sql_extractor/extractor.py

import re
from src.core.prompts import SystemPrompts
from src.core.llm.client import LLMClient
from src.core.sql_extractor.validator import SQLValidator, SQLValidationError
from src.utils.logger import get_logger

logger = get_logger(__name__)

class SQLExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.validator = SQLValidator()
        self.prompts = SystemPrompts()

    def _clean_sql(self, raw_sql: str) -> str:
        """
        Clean the LLM output: remove markdown blocks and extra text.
        """
        if not raw_sql:
            return ""
        
        sql = raw_sql.strip()
        
        # Remove markdown code blocks
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]
        
        # Remove everything before SELECT or WITH
        match = re.search(r'(WITH|SELECT)\s+', sql, re.IGNORECASE)
        if match:
            sql = sql[match.start():]
        
        sql = sql.rstrip(';').strip()
        return sql

    def extract(self, question: str, history: list = None, max_retries: int = 2) -> str:
        """
        Convert user question to SQL query with auto-correction retry loop.
        """
        prompt = self.prompts.sql_generation_prompt(question, history)
        sql = self.llm.generate(prompt)
        sql = self._clean_sql(sql)

        attempt = 0
        while attempt < max_retries:
            try:
                self.validator.validate(sql)
                return sql
            except SQLValidationError as e:
                attempt += 1
                if attempt >= max_retries:
                    raise
                logger.warning(f"SQL validation failed (attempt {attempt}/{max_retries}): {e.message}")
                logger.debug(f"Invalid SQL: {sql}")
                
                # Ask the model to correct the query
                correction_prompt = self.prompts.error_correction_prompt(
                    question=question,
                    error_message=str(e.message),
                    invalid_sql=sql
                )
                sql = self.llm.generate(correction_prompt)
                sql = self._clean_sql(sql)
        
        return sql