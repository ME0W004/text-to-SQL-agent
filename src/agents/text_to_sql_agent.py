# src/agents/text_to_sql_agent.py

import traceback
from src.core.database.connection import DatabaseConnection
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.llm.client import LLMClient
from src.core.prompts import SystemPrompts
from src.core.sql_extractor.validator import SQLValidationError
from src.core.column_mapper_updater import ensure_column_map_is_updated
from src.memory.manager import MemoryManager
from src.utils.config_loader import load_config
from src.utils.logger import get_logger
from src.utils.exceptions import QueryExecutionError

logger = get_logger(__name__)

try:
    from src.core.column_mapper import COLUMN_MAP
except ImportError:
    COLUMN_MAP = {}


class TextToSQLAgent:
    def __init__(self):
        config = load_config()
        self.db = DatabaseConnection(**config)
        self.db.connect()

        ensure_column_map_is_updated(self.db)

        self.llm = LLMClient()
        self.extractor = SQLExtractor(self.llm)
        self.prompts = SystemPrompts()
        self.memory = MemoryManager()

    def _filter_and_translate_rows(self, rows: list) -> list:
        """
        Remove 'Id' columns and translate column names to Persian.
        Uses COLUMN_MAP. Logs missing translations only once per column.
        """
        if not rows:
            return rows

        missing_translations_logged = set()  # ← جلوگیری از هشدارهای تکراری

        filtered_rows = []
        for row in rows:
            new_row = {}
            for key, value in row.items():
                if key.endswith("Id") or key == "Id":
                    continue

                new_key = COLUMN_MAP.get(key)
                if new_key is None:
                    if key not in missing_translations_logged:
                        logger.warning(f"Missing translation for column: '{key}', using original.")
                        missing_translations_logged.add(key)
                    new_key = key

                new_row[new_key] = value
            filtered_rows.append(new_row)

        return filtered_rows

    def ask(self, question: str, session_id: str = None) -> dict:
        # Handle session
        if not session_id:
            session_id = self.memory.create_session()
        elif not self.memory.session_exists(session_id):
            session_id = self.memory.create_session()

        try:
            # Get chat history (last 5 messages)
            history = self.memory.get_history(session_id, limit=5)

            # Step 1: Generate SQL
            sql = self.extractor.extract(question, history=history)
            logger.info(f"Generated SQL: {sql}")

            # Step 2: Execute query
            rows = self.db.execute_query(sql)
            rows = self._filter_and_translate_rows(rows)

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

            # Save conversation
            self.memory.add_message(session_id, "user", question)
            self.memory.add_message(session_id, "assistant", answer)

            return {
                "question": question,
                "sql": sql,
                "answer": answer,
                "row_count": len(rows),
                "rows": rows,
                "success": True,
                "session_id": session_id
            }

        except SQLValidationError as e:
            logger.error(f"SQL validation error: {e}")
            return {
                "success": False,
                "error": f"Invalid SQL: {e.message}",
                "question": question,
                "sql": e.sql if hasattr(e, 'sql') else None,
                "session_id": session_id
            }

        except QueryExecutionError as e:
            logger.error(f"Query execution error: {e}")
            sql = getattr(e, 'sql', None)
            return {
                "success": False,
                "error": f"Database error: {e}",
                "question": question,
                "sql": sql,
                "session_id": session_id
            }

        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            return {
                "success": False,
                "error": f"Internal error: {e}",
                "question": question,
                "sql": None,
                "session_id": session_id
            }

    def close(self):
        self.db.close()