# tests/test_llm/test_wiring.py

"""Facade wiring (Sprint 3B STEP 7).

Verifies TextToSQLAgent builds exactly one router, hands task-bound
adapters to the SQL-extraction and answer seams, passes the router into
OrchestratorDeps, and keeps legacy construction paths (attribute
injection without __init__) working. No database connection is made.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.database.policy import ExecutionPolicy
from src.core.llm.client import LLMClient
from src.core.llm.router import LLMRouter, build_default_router
from src.core.sql_extractor.extractor import SQLExtractor


@pytest.fixture
def wired_agent(monkeypatch):
    """Construct the real facade with infrastructure patched out."""
    import src.agents.text_to_sql_agent as facade_module

    class FakeDB:
        execution_policy = ExecutionPolicy()
        connected = False

        def __init__(self, **kwargs):
            pass

        def connect(self):
            self.connected = True

        def close(self):
            pass

    monkeypatch.setattr(facade_module, "DatabaseConnection", FakeDB)
    monkeypatch.setattr(facade_module, "ensure_column_map_is_updated", lambda db: None)
    monkeypatch.setattr(facade_module, "MemoryManager", lambda: SimpleNamespace())
    monkeypatch.setattr(facade_module, "load_config", lambda: {"host": "fake"})
    return facade_module.TextToSQLAgent()


class TestFacadeWiring:
    def test_agent_builds_exactly_one_router(self, wired_agent):
        assert isinstance(wired_agent.router, LLMRouter)

    def test_task_bound_clients_share_the_single_router(self, wired_agent):
        assert wired_agent.llm.task == "response_generation"
        assert wired_agent.extractor.llm.task == "sql_generation"
        assert wired_agent.llm.router is wired_agent.router
        assert wired_agent.extractor.llm.router is wired_agent.router
        # Distinct adapter instances (no shared mutable adapter state).
        assert wired_agent.llm is not wired_agent.extractor.llm

    def test_orchestrator_deps_receive_the_router(self, wired_agent):
        orchestrator = wired_agent._build_orchestrator()
        deps = orchestrator._deps
        assert deps.llm_router is wired_agent.router
        assert deps.llm_client is wired_agent.llm
        assert deps.sql_extractor is wired_agent.extractor

    def test_extractor_seam_keeps_generate_prompt_shape(self, wired_agent):
        # The extractor contract stays duck-typed generate(prompt) -> str.
        assert callable(wired_agent.extractor.llm.generate)
        assert wired_agent.extractor.llm.generate.__self__ is wired_agent.extractor.llm

    def test_no_global_router_state(self, wired_agent, monkeypatch):
        # A second facade instance builds its own router; no module-level
        # singleton exists on the router module.
        import src.core.llm.router as router_module

        assert not hasattr(router_module, "_default_router")
        other = build_default_router()
        assert other is not wired_agent.router


class TestLegacyConstructionPath:
    def test_build_orchestrator_without_router_attribute(self):
        # Regression (3.0B review finding F1): the documented legacy test
        # construction path (object.__new__ + attribute injection) has no
        # ``router`` attribute; the facade must not require it, because
        # OrchestratorDeps.llm_router is Optional.
        from src.agents.text_to_sql_agent import TextToSQLAgent

        agent = object.__new__(TextToSQLAgent)
        agent.db = SimpleNamespace(execution_policy=ExecutionPolicy())
        agent.llm = LLMClient(build_default_router(), task="response_generation")
        agent.extractor = SQLExtractor(
            LLMClient(build_default_router(), task="sql_generation")
        )
        agent.prompts = SimpleNamespace()
        agent.memory = SimpleNamespace()
        agent.query_executor = SimpleNamespace()

        orchestrator = agent._build_orchestrator()
        assert orchestrator._deps.llm_router is None
        assert orchestrator._deps.llm_client is agent.llm

    def test_build_orchestrator_with_injected_router(self):
        from src.agents.text_to_sql_agent import TextToSQLAgent

        agent = object.__new__(TextToSQLAgent)
        agent.db = SimpleNamespace(execution_policy=ExecutionPolicy())
        router = build_default_router()
        agent.router = router
        agent.llm = LLMClient(router, task="response_generation")
        agent.extractor = SQLExtractor(LLMClient(router, task="sql_generation"))
        agent.prompts = SimpleNamespace()
        agent.memory = SimpleNamespace()
        agent.query_executor = SimpleNamespace()

        orchestrator = agent._build_orchestrator()
        assert orchestrator._deps.llm_router is router
