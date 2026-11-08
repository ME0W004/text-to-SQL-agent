# src/core/sql_extractor/extractor.py

import re
import logging
from time import perf_counter
from src.core.prompts import SystemPrompts
from src.core.sql_extractor.validator import SQLValidator, SQLValidationError
from src.utils.logger import get_logger, log_event, sql_fingerprint

logger = get_logger(__name__)

class SQLExtractor:
    """LLM-driven SQL generation with a bounded prompt-correction retry loop.

    ``llm_client`` is any object exposing ``generate(prompt) -> str``.
    Since Sprint 3A this is a task-bound adapter over the LLMRouter
    (``src/core/llm/router.py``): this class no longer imports any LLM
    module and performs no provider selection — provider choice, retry
    and fallback live in the router layer.
    """

    def __init__(self, llm_client):
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
        started = perf_counter()
        log_event("llm_call_started", component="llm", purpose="sql_generation")
        try:
            prompt = self.prompts.sql_generation_prompt(question, history)
            sql = self.llm.generate(prompt)
        except Exception as exc:
            log_event("llm_call_failed", level=logging.ERROR, component="llm", purpose="sql_generation",
                      error_type=type(exc).__name__, duration_ms=round((perf_counter() - started) * 1000, 3))
            raise
        log_event("llm_call_completed", component="llm", purpose="sql_generation",
                  duration_ms=round((perf_counter() - started) * 1000, 3))
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
                log_event("sql_validation_failed", level=logging.WARNING, component="security",
                          reason_code=getattr(e, "code", "SQL_VALIDATION_ERROR"), attempt=attempt,
                          sql_fingerprint=sql_fingerprint(sql))
                
                # Ask the model to correct the query
                correction_prompt = self.prompts.error_correction_prompt(
                    question=question,
                    error_message=str(e.message),
                    invalid_sql=sql
                )
                started = perf_counter()
                log_event("llm_call_started", component="llm", purpose="sql_correction", attempt=attempt)
                try:
                    sql = self.llm.generate(correction_prompt)
                except Exception as exc:
                    log_event("llm_call_failed", level=logging.ERROR, component="llm", purpose="sql_correction",
                              attempt=attempt, error_type=type(exc).__name__,
                              duration_ms=round((perf_counter() - started) * 1000, 3))
                    raise
                log_event("llm_call_completed", component="llm", purpose="sql_correction", attempt=attempt,
                          duration_ms=round((perf_counter() - started) * 1000, 3))
                sql = self._clean_sql(sql)
        
        return sql
