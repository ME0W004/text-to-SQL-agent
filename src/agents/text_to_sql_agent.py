# src/agents/text_to_sql_agent.py

from src.core.database.connection import DatabaseConnection
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.llm.client import LLMClient
from src.core.prompts import SystemPrompts
from src.core.sql_extractor.validator import SQLValidationError
from src.core.column_mapper_updater import ensure_column_map_is_updated
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.exceptions import QueryExecutionError

# Import column mapping for translation
try:
    from src.core.column_mapper import COLUMN_MAP
except ImportError:
    COLUMN_MAP = {}  # Fallback to empty dict if file not found

logger = get_logger(__name__)


class TextToSQLAgent:
    def __init__(self):
        config = load_config()
        self.db = DatabaseConnection(**config)
        self.db.connect()

        # Ensure column map is up-to-date with the current schema
        ensure_column_map_is_updated(self.db)

        self.llm = LLMClient()
        self.extractor = SQLExtractor(self.llm)
        self.prompts = SystemPrompts()

    def _filter_and_translate_rows(self, rows: list) -> list:
        """
        Remove 'Id' columns and translate column names to Persian.
        Uses the full COLUMN_MAP from column_mapper.py.
        """
        if not rows:
            return rows

        filtered_rows = []
        for row in rows:
            new_row = {}
            for key, value in row.items():
                # Skip columns that end with "Id" or are exactly "Id"
                if key.endswith("Id") or key == "Id":
                    continue
                # Try to get Persian translation, fallback to original if missing
                new_key = COLUMN_MAP.get(key)
                if new_key is None:
                    logger.warning(f"Missing translation for column: {key}, using original.")
                    new_key = key
                new_row[new_key] = value
            filtered_rows.append(new_row)
        return filtered_rows

    def ask(self, question: str, session_id: str = None) -> dict:
        """
        Process a natural language question and return the answer.
        """
        try:
            # Step 1: Generate SQL from the question
            sql = self.extractor.extract(question)

            # Step 2: Execute the SQL query on the database
            rows = self.db.execute_query(sql)

            # Step 3: Filter out 'Id' columns and translate column names to Persian
            rows = self._filter_and_translate_rows(rows)

            # Step 4: Generate a natural language response from the results
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
        """Close the database connection."""
        self.db.close()