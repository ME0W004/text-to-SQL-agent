"""Backward compatibility tests for Sprint 2 Prompt 2B.

Sprint 2 Prompt 2B introduces the Orchestrator and contracts without
breaking the public API, CLI, evaluation adapter, or session behavior.
These tests verify that:

* The `/api/v1/ask` request/response schema is unchanged.
* The CLI (`run.py`) can still construct and call the agent.
* Session behavior is unchanged (memory round-trip works).
* The Evaluation adapter still works against the public Agent façade.
* The ``TextToSQLAgent.ask()`` public dict format is unchanged.
"""

from __future__ import annotations

import pytest

from src.agents.text_to_sql_agent import TextToSQLAgent
from src.agents.orchestrator import Orchestrator, OrchestratorDeps
from src.agents.adapters import (
    QueryExecutorAdapter,
    SecurityValidatorAdapter,
)
from src.agents.state import (
    AgentState,
    create_initial_state,
    state_to_api_response,
)
from src.api.models import AskRequest, AskResponse


# ---------------------------------------------------------------------------
# Public API contract
# ---------------------------------------------------------------------------


class TestAskRequestUnchanged:
    def test_ask_request_schema(self) -> None:
        fields = AskRequest.model_fields
        assert "question" in fields
        assert "session_id" in fields

    def test_ask_response_schema(self) -> None:
        fields = AskResponse.model_fields
        assert "success" in fields
        assert "question" in fields
        assert "answer" in fields
        assert "sql" in fields
        assert "row_count" in fields
        assert "rows" in fields
        assert "error" in fields
        assert "session_id" in fields


class TestStateToApiResponseUnchanged:
    """``state_to_api_response(state)`` must continue to produce the
    documented public dict format."""

    def test_success_response_shape(self) -> None:
        state = create_initial_state(
            question="q", session_id="s1",
        )
        # Move through the public state-applier chain.
        state = state.with_generated_sql("SELECT 1 AS v")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"v": 1}], 12.0)
        state = state.with_answer("answer")
        response = state_to_api_response(state)

        assert set(response.keys()) == {
            "success", "question", "answer", "sql",
            "row_count", "rows", "session_id",
        }
        assert response["success"] is True
        assert response["question"] == "q"
        assert response["answer"] == "answer"
        assert response["sql"] == "SELECT 1 AS v"
        assert response["row_count"] == 1
        assert response["rows"] == [{"v": 1}]
        assert response["session_id"] == "s1"

    def test_failure_response_shape(self) -> None:
        state = create_initial_state(
            question="q", session_id="s1",
        )
        state = state.with_generated_sql("SELECT * FROM bogus;")
        state = state.with_security_rejected(
            "NON_READ_ONLY_STATEMENT", "rejected",
        )
        response = state_to_api_response(state)

        assert response["success"] is False
        assert response["error"]
        assert response["sql"] == "SELECT * FROM bogus;"
        assert response["session_id"] == "s1"


# ---------------------------------------------------------------------------
# TextToSQLAgent public surface
# ---------------------------------------------------------------------------


class TestTextToSQLAgentCompatibility:
    """``TextToSQLAgent.ask()`` must continue to return a dict in the
    documented format."""

    def test_ask_returns_dict_with_success_key(self):
        class FakeExtractor:
            def extract(self, question, history=None):
                return "SELECT 1 AS v"

        class FakeLLM:
            def generate(self, prompt):
                return "answer"

        class FakeMemory:
            def __init__(self):
                self.session = "s1"
                self.messages = []

            def create_session(self):
                return self.session

            def session_exists(self, sid):
                return bool(sid)

            def get_history(self, sid, limit=5):
                return []

            def add_message(self, sid, role, content):
                self.messages.append((role, content))

            def clear_session(self, sid):
                pass

        class FakePrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        class FakeCursor:
            arraysize = 1

            def __init__(self, rows):
                self.rows = list(rows)
                self.executed = []
                self.closed = False

            def execute(self, sql, params=None):
                self.executed.append(sql)

            def fetchmany(self, size):
                result, self.rows = self.rows[:size], self.rows[size:]
                return result

            def close(self):
                self.closed = True

        class FakeConnection:
            def __init__(self, cursor):
                self.cursor_instance = cursor
                self.closed = False

            def cursor(self, as_dict=True):
                return self.cursor_instance

            def close(self):
                self.closed = True

        from src.core.database.connection import DatabaseConnection, ExecutionPolicy

        cursor = FakeCursor([{"v": 1}])
        db = DatabaseConnection(
            "host", 1433, "user", "password", "database",
            execution_policy=ExecutionPolicy(max_rows=10, max_fetch_batch=2,
                                             query_timeout=5, connection_timeout=2),
            connect_fn=lambda **kwargs: FakeConnection(cursor),
        )

        agent = object.__new__(TextToSQLAgent)
        agent.db = db
        agent.extractor = FakeExtractor()
        agent.llm = FakeLLM()
        agent.memory = FakeMemory()
        agent.prompts = FakePrompts()
        agent.query_executor = QueryExecutorAdapter(db)

        result = agent.ask("how many?")
        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is True
        assert result["answer"] == "answer"
        assert result["rows"] == [{"v": 1}]


# ---------------------------------------------------------------------------
# Evaluation adapter compatibility
# ---------------------------------------------------------------------------


class TestEvaluationAdapterCompatibility:
    """The evaluation adapter consumes the public ``Agent`` interface
    (the ``EvaluationAgentAdapter`` wraps ``TextToSQLAgent``). Sprint 2
    Prompt 2B must not break this path."""

    def test_evaluation_adapter_normalizes_ask_response(self):
        from evaluation.adapters import EvaluationAgentAdapter

        class StubAgent:
            def ask(self, question, session_id=None):
                return {
                    "success": True,
                    "question": question,
                    "answer": "ok",
                    "sql": "SELECT 1",
                    "row_count": 1,
                    "rows": [{"v": 1}],
                    "session_id": session_id or "s-new",
                }

        adapter = EvaluationAgentAdapter(agent=StubAgent())
        response = adapter.ask("how many?")
        assert response.success is True
        assert response.answer == "ok"
        assert response.sql == "SELECT 1"
        assert response.row_count == 1


# ---------------------------------------------------------------------------
# Session behaviour
# ---------------------------------------------------------------------------


class TestSessionBehaviour:
    """Session creation, lookup, and persistence continue to work via
    the orchestrator path."""

    def test_session_round_trip_through_orchestrator(self, tmp_path):
        from src.memory.manager import MemoryManager
        from src.agents.adapters import (
            QueryExecutorAdapter,
            SecurityValidatorAdapter,
        )
        from src.agents.orchestrator import Orchestrator, OrchestratorDeps

        class FakeExtractor:
            def extract(self, question, history=None):
                return "SELECT 1 AS v"

        class FakeLLM:
            def generate(self, prompt):
                return "answer"

        class FakePrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        class FakeCursor:
            arraysize = 1

            def __init__(self, rows):
                self.rows = list(rows)
                self.executed = []
                self.closed = False

            def execute(self, sql, params=None):
                self.executed.append(sql)

            def fetchmany(self, size):
                result, self.rows = self.rows[:size], self.rows[size:]
                return result

            def close(self):
                self.closed = True

        class FakeConnection:
            def __init__(self, cursor):
                self.cursor_instance = cursor
                self.closed = False

            def cursor(self, as_dict=True):
                return self.cursor_instance

            def close(self):
                self.closed = True

        from src.core.database.connection import DatabaseConnection, ExecutionPolicy

        cursor = FakeCursor([{"v": 1}])
        db = DatabaseConnection(
            "host", 1433, "user", "password", "database",
            execution_policy=ExecutionPolicy(max_rows=10, max_fetch_batch=2,
                                             query_timeout=5, connection_timeout=2),
            connect_fn=lambda **kwargs: FakeConnection(cursor),
        )

        memory = MemoryManager(db_path=str(tmp_path / "memory.db"))
        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=QueryExecutorAdapter(db),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=memory,
            security_validator=SecurityValidatorAdapter(),
        )
        orch = Orchestrator(deps)
        state = create_initial_state(question="q", session_id=None)
        final = orch.run(state)
        assert final.request.session_id is not None
        assert memory.session_exists(final.request.session_id)
        # The session has been populated with user + assistant messages.
        history = memory.get_history(final.request.session_id, limit=10)
        roles = [m["role"] for m in history]
        assert "user" in roles
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# CLI compatibility
# ---------------------------------------------------------------------------


class TestCliCompatibility:
    """``run.py`` constructs ``TextToSQLAgent()`` with no arguments. The
    constructor signature must remain unchanged."""

    def test_constructor_signature_unchanged(self) -> None:
        import inspect
        sig = inspect.signature(TextToSQLAgent.__init__)
        # No required parameters.
        for param in sig.parameters.values():
            if param.name == "self":
                continue
            assert param.default is not inspect.Parameter.empty, (
                f"Parameter {param.name} must have a default to preserve "
                "backward compatibility"
            )


# ---------------------------------------------------------------------------
# Database compatibility re-exports (Sprint 2 Prompt 3C)
# ---------------------------------------------------------------------------


class TestDatabasePackageExportSurface:
    """The lazy ``src.core.database`` package must behave like the eager
    module it replaced: ``dir()`` lists every public name, unknown
    attributes still raise ``AttributeError``, and every package export
    is the *same object* as both the canonical ``policy`` definition and
    the ``connection`` compatibility re-export (no duplicates)."""

    def test_identity_chain_package_policy_connection(self) -> None:
        from src.core.database import connection, policy
        import src.core.database as pkg

        for name in ("ExecutionPolicy", "DatabaseError", "DatabaseErrorCode"):
            package_obj = getattr(pkg, name)
            assert package_obj is getattr(policy, name), (
                f"src.core.database.{name} is not the canonical policy object"
            )
            assert package_obj is getattr(connection, name), (
                f"src.core.database.{name} is not the connection re-export"
            )

    def test_dir_includes_all_public_names(self) -> None:
        import src.core.database as pkg

        listing = dir(pkg)
        for name in pkg.__all__:
            assert name in listing, (
                f"dir(src.core.database) is missing {name!r}"
            )

    def test_unknown_attribute_still_raises(self) -> None:
        import src.core.database as pkg

        with pytest.raises(AttributeError):
            getattr(pkg, "does_not_exist")

    def test_connection_all_is_the_compatibility_surface(self) -> None:
        """``connection.py`` re-exports exactly the three moved value
        objects for backwards compatibility — its ``__all__`` must still
        contain them so ``from ... import *`` consumers keep working."""
        from src.core.database import connection

        for name in ("ExecutionPolicy", "DatabaseError", "DatabaseErrorCode"):
            assert name in connection.__all__, (
                f"connection.__all__ lost {name!r} (compatibility re-export)"
            )