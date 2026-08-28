# src/agents/text_to_sql_agent.py

import logging
from time import perf_counter
from dataclasses import replace
from src.core.database.connection import DatabaseConnection
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.llm.client import LLMClient
from src.core.prompts import SystemPrompts
from src.core.sql_extractor.validator import SQLValidationError
from src.core.security.sql_security import SQLSecurityError, SecurityReasonCode
from src.core.column_mapper_updater import ensure_column_map_is_updated
from src.memory.manager import MemoryManager
from src.utils.config_loader import load_config
from src.utils.logger import (
    get_logger,
    get_trace_context,
    log_event,
    reset_trace_context,
    set_trace_context,
    sql_fingerprint,
    update_session_id,
)
from src.utils.exceptions import QueryExecutionError
from src.agents.state import (
    AgentState,
    create_initial_state,
    state_to_api_response,
    PipelineStage,
    ValidationStatus,
    ExecutionStatus,
    ErrorCategory,
)

logger = get_logger(__name__)

try:
    from src.core.column_mapper import COLUMN_MAP
except ImportError:
    COLUMN_MAP = {}


class TextToSQLAgent:
    def __init__(self):
        config = load_config()
        self.db = DatabaseConnection(**config)
        try:
            self.db.connect()
            ensure_column_map_is_updated(self.db)
        finally:
            self.db.close()

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
        # Get or create trace context
        owns_context = get_trace_context() is None
        trace_ctx = get_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        
        started = perf_counter()
        
        trace_token = set_trace_context(session_id=session_id) if owns_context else None
        
        # Create initial AgentState
        state = create_initial_state(
            question=question,
            session_id=session_id,
            trace_id=trace_id,
        )
        
        # Handle session - use state for tracking
        if not session_id:
            session_id = self.memory.create_session()
            state = replace(state, request=state.request.with_session(session_id))
        elif not self.memory.session_exists(session_id):
            session_id = self.memory.create_session()
            state = replace(state, request=state.request.with_session(session_id))
        
        update_session_id(session_id)
        state = replace(state, request=state.request.with_session(session_id))

        try:
            log_event("agent_started", component="agent", 
                      question_length=len(question),
                      trace_id=state.request.trace_id,
                      session_id=session_id)

            # Get chat history (last 5 messages)
            history = self.memory.get_history(session_id, limit=5)
            state = replace(state, conversation=state.conversation.with_history(history))

            # Step 1: Generate SQL
            sql = self.extractor.extract(question, history=history)
            state = state.with_generated_sql(sql)
            log_event("sql_generated", component="agent", 
                      sql_fingerprint=state.sql.sql_fingerprint,
                      trace_id=state.request.trace_id)

            # Step 2: Execute query
            state = state.with_validation_passed()
            
            exec_started = perf_counter()
            rows = self.db.execute_query(sql)
            exec_duration = (perf_counter() - exec_started) * 1000
            
            rows = self._filter_and_translate_rows(rows)
            state = state.with_execution_results(rows, exec_duration)

            # Step 3: Generate natural language response
            if not rows:
                answer = "No data found for your query."
            else:
                prompt = self.prompts.natural_response_prompt(
                    question=question,
                    rows=rows,
                    row_count=len(rows)
                )
                llm_started = perf_counter()
                log_event("llm_call_started", component="llm", purpose="response_generation",
                          trace_id=state.request.trace_id)
                try:
                    answer = self.llm.generate(prompt)
                except Exception as exc:
                    log_event("llm_call_failed", level=logging.ERROR, component="llm",
                              purpose="response_generation", error_type=type(exc).__name__,
                              duration_ms=round((perf_counter() - llm_started) * 1000, 3),
                              trace_id=state.request.trace_id)
                    raise
                llm_duration = (perf_counter() - llm_started) * 1000
                state = state.with_llm_call(llm_duration)
                log_event("llm_call_completed", component="llm", purpose="response_generation",
                          duration_ms=round(llm_duration, 3),
                          trace_id=state.request.trace_id)
            
            state = state.with_answer(answer)
            log_event("response_generated", component="agent", 
                      row_count=len(rows),
                      trace_id=state.request.trace_id)
            log_event("agent_completed", component="agent",
                      row_count=len(rows),
                      duration_ms=round((perf_counter() - started) * 1000, 3),
                      trace_id=state.request.trace_id)

            # Save conversation
            self.memory.add_message(session_id, "user", question)
            self.memory.add_message(session_id, "assistant", answer)

            # Convert to API response format (backward compatible)
            return state_to_api_response(state)

        except (SQLValidationError, SQLSecurityError) as e:
            log_event("agent_failed", level=logging.WARNING, component="agent",
                      error_type=type(e).__name__, error_code="SQL_VALIDATION_ERROR",
                      duration_ms=round((perf_counter() - started) * 1000, 3),
                      trace_id=state.request.trace_id if 'state' in locals() else None)
            
            # Try to extract error info
            reason_code = getattr(e, 'code', None)
            if hasattr(reason_code, 'value'):
                reason_code = reason_code.value
            
            # Update state with error
            if 'state' in locals():
                error_msg = getattr(e, 'message', str(e))
                # Maintain backward compatibility: prefix with "Invalid SQL: "
                compat_error_msg = f"Invalid SQL: {error_msg}"
                
                if isinstance(e, SQLSecurityError) or (reason_code and 'SECURITY' in str(reason_code).upper()):
                    state = state.with_security_rejected(
                        reason_code or "SECURITY_REJECTED",
                        compat_error_msg
                    )
                else:
                    state = state.with_validation_failed(
                        compat_error_msg,
                        reason_code
                    )
                return state_to_api_response(state)
            
            # Fallback for unexpected state
            return {
                "success": False,
                "error": f"Invalid SQL: {getattr(e, 'message', str(e))}",
                "question": question,
                "sql": getattr(e, 'sql', None),
                "session_id": session_id
            }

        except QueryExecutionError as e:
            log_event("agent_failed", level=logging.ERROR, component="agent",
                      error_type=type(e).__name__, 
                      error_code=getattr(getattr(e, "code", None), "value", None),
                      duration_ms=round((perf_counter() - started) * 1000, 3),
                      trace_id=state.request.trace_id if 'state' in locals() else None)
            
            sql = getattr(e, 'sql', None)
            
            if 'state' in locals():
                db_error_code = getattr(e.code, 'value', None) if hasattr(e, 'code') else None
                state = state.with_execution_error(
                    status=ExecutionStatus.FAILED,
                    error_code=db_error_code or "DATABASE_QUERY_ERROR",
                    error_message=str(e),
                    database_error_code=db_error_code,
                    duration_ms=None
                )
                return state_to_api_response(state)
            
            return {
                "success": False,
                "error": f"Database error: {e}",
                "question": question,
                "sql": sql,
                "session_id": session_id
            }

        except Exception as e:
            log_event("agent_failed", level=logging.ERROR, component="agent",
                      error_type=type(e).__name__,
                      duration_ms=round((perf_counter() - started) * 1000, 3),
                      trace_id=state.request.trace_id if 'state' in locals() else None)
            
            if 'state' in locals():
                state = state.with_error_response(
                    error="Internal error.",
                    category=ErrorCategory.INTERNAL,
                    code=type(e).__name__,
                    stage=state.pipeline_stage,
                    retryable=False
                )
                return state_to_api_response(state)
            
            return {
                "success": False,
                "error": "Internal error.",
                "question": question,
                "sql": None,
                "session_id": session_id
            }
        finally:
            if owns_context and trace_token is not None:
                reset_trace_context(trace_token)

    def close(self):
        self.db.close()