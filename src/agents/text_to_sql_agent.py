# src/agents/text_to_sql_agent.py

"""Compatibility façade — thin layer over the Orchestrator.

Sprint 2 Prompt 2B turns :class:`TextToSQLAgent` into a thin façade.
The orchestration logic now lives in
:class:`src.agents.orchestrator.Orchestrator`. This class continues to
own:

* Construction of the dependencies (``OrchestratorDeps``).
* Trace-context lifecycle (``set_trace_context`` / ``reset_trace_context``).
* Backward-compatible public API (``ask(question, session_id)``).

The public API returns ``state_to_api_response(state)`` so all existing
callers (CLI, Streamlit, API, Evaluation) continue to work unchanged.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Mapping, Optional

from src.core.database.connection import DatabaseConnection
from src.core.llm.client import LLMClient
from src.core.prompts import SystemPrompts
from src.core.sql_extractor.extractor import SQLExtractor
from src.core.column_mapper_updater import ensure_column_map_is_updated
from src.memory.manager import MemoryManager
from src.utils.config_loader import load_config
from src.utils.logger import (
    get_logger,
    get_trace_context,
    log_event,
    reset_trace_context,
    set_trace_context,
    update_session_id,
)

from src.agents.adapters import QueryExecutorAdapter
from src.agents.orchestrator import Orchestrator, OrchestratorDeps
from src.agents.state import (
    create_initial_state,
    state_to_api_response,
)


logger = get_logger(__name__)

try:
    from src.core.column_mapper import COLUMN_MAP
except ImportError:
    COLUMN_MAP = {}


class TextToSQLAgent:
    """Compatibility façade over the new Orchestrator.

    Constructor signature is preserved (``TextToSQLAgent()``) so the
    API, CLI, Streamlit UI and evaluation harness keep working without
    modification. All orchestration logic has moved into
    :class:`Orchestrator`; this class is responsible only for wiring
    and trace-context handling.
    """

    def __init__(self) -> None:
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

        # Optional injection points. Existing tests may replace any of
        # these attributes before calling ``ask``. The Orchestrator picks
        # them up lazily at ``ask`` time.
        self.query_executor = QueryExecutorAdapter(self.db)
        self.orchestrator: Optional[Orchestrator] = None

    def _build_orchestrator(self) -> Orchestrator:
        query_executor = getattr(self, "query_executor", None) or QueryExecutorAdapter(self.db)
        deps = OrchestratorDeps(
            sql_extractor=self.extractor,
            query_executor=query_executor,
            llm_client=self.llm,
            prompts=self.prompts,
            memory_manager=self.memory,
            execution_policy=self.db.execution_policy,
        )
        return Orchestrator(deps)

    def ask(self, question: str, session_id: Optional[str] = None) -> dict[str, Any]:
        """Run the pipeline and return the API response dict."""
        trace_ctx = get_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        owns_context = trace_ctx is None
        trace_token = set_trace_context(session_id=session_id) if owns_context else None

        started = perf_counter()
        try:
            log_event(
                "agent_started",
                component="agent",
                question_length=len(question),
                trace_id=trace_id,
            )
            state = create_initial_state(
                question=question,
                session_id=session_id,
                trace_id=trace_id,
            )

            orchestrator = getattr(self, "orchestrator", None) or self._build_orchestrator()
            state = orchestrator.run(state)

            update_session_id(state.request.session_id)
            log_event(
                "agent_completed" if state.is_success() else "agent_failed",
                component="agent",
                success=state.is_success(),
                duration_ms=round((perf_counter() - started) * 1000, 3),
                trace_id=state.request.trace_id,
            )
            return state_to_api_response(state)
        finally:
            if owns_context and trace_token is not None:
                reset_trace_context(trace_token)

    def close(self) -> None:
        self.db.close()


__all__ = ["TextToSQLAgent"]