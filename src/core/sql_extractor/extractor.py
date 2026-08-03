# src/core/sql_extractor/extractor.py

import re
from src.core.prompts import SystemPrompts
from src.core.llm.client import LLMClient
from src.core.sql_extractor.validator import SQLValidator, SQLValidationError

class SQLExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.validator = SQLValidator()
        self.prompts = SystemPrompts()

    def _clean_sql(self, raw_sql: str) -> str:
        
        if not raw_sql:
            return ""
        
        sql = raw_sql.strip()
        
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        if sql.endswith("```"):
            sql = sql[:-3]
        
        match = re.search(r'(WITH|SELECT)\s+', sql, re.IGNORECASE)
        if match:
            sql = sql[match.start():]
        
        sql = sql.rstrip(';').strip()
        
        return sql

    def extract(self, question: str, history: list = None) -> str:
        
        prompt = self.prompts.sql_generation_prompt(question, history)
        
        raw_sql = self.llm.generate(prompt)
        
        sql = self._clean_sql(raw_sql)
        
        try:
            self.validator.validate(sql)
        except SQLValidationError as e:
            raise SQLValidationError(f"{e.message}", sql=sql)
        
        return sql