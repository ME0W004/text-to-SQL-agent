# src/agents/text_to_sql_agent.py

from src.core.database.connection import DatabaseConnection
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.llm.client import LLMClient
from src.core.prompts import SystemPrompts
from src.core.sql_extractor.validator import SQLValidationError
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.exceptions import QueryExecutionError

logger = get_logger(__name__)

class TextToSQLAgent:
    def __init__(self):
        config = load_config()
        self.db = DatabaseConnection(**config)
        self.db.connect()
        self.llm = LLMClient()
        self.extractor = SQLExtractor(self.llm)
        self.prompts = SystemPrompts()

    def ask(self, question: str, session_id: str = None) -> dict:
        
        try:
            # Step 1: Generate SQL
            sql = self.extractor.extract(question)

            # Step 2: Execute query
            rows = self.db.execute_query(sql)

            # Step 3: Generate natural language response
            if not rows:
                answer = "No data found for your query."
            else:
                prompt = self.prompts.natural_response_prompt(
                    question=question,
                    rows=rows,
                    row_count=len(rows)
                )
                answer = self.llm.generate(prompt)

            return {
                "question": question,
                "sql": sql,
                "answer": answer,
                "row_count": len(rows),
                "rows": rows,
                "success": True
            }

        except SQLValidationError as e:
            logger.error(f"SQL validation error: {e}")
            return {
                "success": False,
                "error": f"Invalid SQL: {e.message}",
                "question": question,
                "sql": e.sql if hasattr(e, 'sql') else None
            }
        except QueryExecutionError as e:
            logger.error(f"Query execution error: {e}")
            return {
                "success": False,
                "error": f"Database error: {e}",
                "question": question
            }
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {
                "success": False,
                "error": f"Internal error: {e}",
                "question": question
            }

    def close(self):
        self.db.close()