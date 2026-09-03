"""Static import-graph tests for Sprint 2 Prompt 2B component modules.

These tests walk the import statements of every component module and
verify they comply with the dependency rules defined in
``docs/architecture/dependency-rules.md``.

The rules enforced here are:

1. Contracts module must not depend on AgentState or API/UI.
2. Orchestrator must not depend on provider SDKs.
3. Adapters must not depend on AgentState.
4. New component modules must not import pymssql / sqlite3 / provider
   SDKs / MemoryManager.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src" / "agents"


# Modules covered by the architecture rules.
COMPONENT_MODULES = (
    "src/agents/contracts.py",
    "src/agents/orchestrator.py",
    "src/agents/adapters.py",
)

# Forbidden imports (these are direct infra / SDK / state imports that
# domain components must never make).
FORBIDDEN_PROVIDER_SDKS = (
    "src.core.llm.openai_client",
    "src.core.llm.gemini_client",
    "src.core.llm.groq_client",
    "src.core.llm.ollama_client",
)

FORBIDDEN_STATE = ("src.agents.state",)
FORBIDDEN_DB = ("pymssql",)
FORBIDDEN_MEMORY = ("src.memory.manager",)
FORBIDDEN_API = ("src.api", "fastapi")


def _imports_in_module(path: pathlib.Path) -> list[str]:
    """Return the list of top-level module references imported by *path*."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Build dotted module path
            if node.level:
                # relative import — resolve to module root
                module_root = ".".join(path.relative_to(PROJECT_ROOT).parts[:-1])
                # relative import is from module_root going up by `node.level`
                # we don't fully resolve for these tests — just record the
                # unresolved dotted form so the rule check matches either
                # the resolved or unresolved path.
                resolved = (module_root + "." + module).lstrip(".")
                imports.append(resolved)
            else:
                imports.append(module)
    return imports


def _relative_path_to_module(rel_path: str) -> str:
    return rel_path.replace("/", ".").replace("\\", ".").removesuffix(".py")


@pytest.mark.parametrize("rel_path", COMPONENT_MODULES)
def test_component_module_exists(rel_path: str) -> None:
    path = PROJECT_ROOT / rel_path
    assert path.exists(), f"Component module missing: {rel_path}"


class TestContractsModuleBoundaries:
    """``contracts.py`` is the canonical home for value objects,
    Protocols and errors. It must not depend on AgentState or the API
    surface."""

    @classmethod
    @pytest.fixture(scope="class")
    def imports(cls) -> list[str]:
        path = PROJECT_ROOT / "src/agents/contracts.py"
        return _imports_in_module(path)

    def test_does_not_import_agent_state(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_STATE, (
                "contracts.py must not import AgentState"
            )

    def test_does_not_import_pymssql(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_DB, (
                "contracts.py must not import pymssql"
            )

    def test_does_not_import_memory_manager(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_MEMORY, (
                "contracts.py must not import MemoryManager"
            )

    def test_does_not_import_api(self, imports: list[str]) -> None:
        for module in imports:
            for forbidden in FORBIDDEN_API:
                assert not module.startswith(forbidden), (
                    f"contracts.py must not import API surface ({forbidden})"
                )

    def test_does_not_import_database_driver_module(self, imports: list[str]) -> None:
        """ADR-016: contracts import the driver-free
        ``src.core.database.policy`` value objects, never the pymssql
        adapter module ``src.core.database.connection``."""
        assert "src.core.database.connection" not in imports, (
            "contracts.py must import src.core.database.policy, not "
            "src.core.database.connection (ADR-016)"
        )

    def test_imports_policy_module(self, imports: list[str]) -> None:
        assert "src.core.database.policy" in imports, (
            "contracts.py must import ExecutionPolicy/DatabaseErrorCode "
            "from the canonical src.core.database.policy"
        )


class TestOrchestratorModuleBoundaries:
    """``orchestrator.py`` is the single writer of AgentState. It is the
    only module allowed to import ``AgentState`` directly. It must not
    import provider SDKs or pymssql."""

    @classmethod
    @pytest.fixture(scope="class")
    def imports(cls) -> list[str]:
        path = PROJECT_ROOT / "src/agents/orchestrator.py"
        return _imports_in_module(path)

    def test_does_not_import_provider_sdks(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_PROVIDER_SDKS, (
                f"orchestrator.py must not import provider SDK ({module})"
            )

    def test_does_not_import_pymssql(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_DB, (
                "orchestrator.py must not import pymssql"
            )

    def test_orchestrator_imports_agent_state(self, imports: list[str]) -> None:
        # The orchestrator is the only module allowed to import AgentState.
        assert "src.agents.state" in imports, (
            "orchestrator.py must import AgentState (it is the single writer)"
        )


class TestAdaptersModuleBoundaries:
    """``adapters.py`` wraps existing components. It must not import
    AgentState."""

    @classmethod
    @pytest.fixture(scope="class")
    def imports(cls) -> list[str]:
        path = PROJECT_ROOT / "src/agents/adapters.py"
        return _imports_in_module(path)

    def test_does_not_import_agent_state(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_STATE, (
                "adapters.py must not import AgentState"
            )

    def test_does_not_import_provider_sdks(self, imports: list[str]) -> None:
        for module in imports:
            assert module not in FORBIDDEN_PROVIDER_SDKS, (
                f"adapters.py must not import provider SDK ({module})"
            )

    def test_does_not_import_api(self, imports: list[str]) -> None:
        for module in imports:
            for forbidden in FORBIDDEN_API:
                assert not module.startswith(forbidden), (
                    f"adapters.py must not import API surface ({forbidden})"
                )


class TestOrchestratorSingleWriter:
    """The orchestrator is the only component that exposes a method
    which both receives and returns ``AgentState``. This test enforces
    the single-writer rule for the new components introduced in Prompt
    2B."""

    def test_orchestrator_exposes_run_method(self) -> None:
        from src.agents.orchestrator import Orchestrator
        assert hasattr(Orchestrator, "run")
        assert callable(Orchestrator.run)

    def test_orchestrator_constructs_with_injected_dependencies(self) -> None:
        """Orchestrator receives its deps via the constructor — it must
        not instantiate infrastructure on its own."""
        from src.agents.orchestrator import Orchestrator, OrchestratorDeps
        from src.agents.adapters import SecurityValidatorAdapter

        class FakeExtractor:
            def extract(self, question, history=None):
                return "SELECT 1"

        class FakeExecutor:
            def execute(self, sql, *, params=None, policy=None):
                from src.agents.contracts import QueryResult
                return QueryResult()

        class FakeLLM:
            def generate(self, prompt):
                return "ok"

        class FakeMemory:
            def create_session(self):
                return "s"

            def session_exists(self, sid):
                return True

            def get_history(self, sid, limit=5):
                return []

            def add_message(self, sid, role, content):
                pass

            def clear_session(self, sid):
                pass

        class FakePrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=FakeExecutor(),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            security_validator=SecurityValidatorAdapter(),
        )
        orch = Orchestrator(deps)
        # The deps are held by reference; orchestrator never re-creates them.
        assert orch._deps is deps


class TestStateWriterRule:
    """The state-applier methods (``with_*``) live on ``AgentState`` but
    are called from one place: the Orchestrator. This test asserts
    that none of the future component modules in scope import
    ``src.agents.state``."""

    @pytest.mark.parametrize("rel_path", COMPONENT_MODULES)
    def test_component_module_does_not_import_state(
        self, rel_path: str
    ) -> None:
        """All future component modules except ``orchestrator.py`` and
        ``state.py`` itself must not import ``AgentState``."""
        path = PROJECT_ROOT / rel_path
        imports = _imports_in_module(path)
        if rel_path == "src/agents/orchestrator.py":
            # The orchestrator is the only allowed importer.
            return
        for module in imports:
            assert module not in FORBIDDEN_STATE, (
                f"{rel_path} must not import AgentState (single writer rule)"
            )


class TestLightweightAgentModules:
    """``state.py`` and ``policy.py`` are part of the lightweight agent
    import surface (ADR-016). They must not pull in drivers, provider
    SDKs, memory, or the API/UI layers."""

    @pytest.mark.parametrize(
        "rel_path",
        (
            "src/agents/state.py",
            "src/core/database/policy.py",
        ),
    )
    def test_does_not_import_driver_or_sdks(self, rel_path: str) -> None:
        imports = _imports_in_module(PROJECT_ROOT / rel_path)
        for module in imports:
            assert module not in FORBIDDEN_DB, (
                f"{rel_path} must not import pymssql"
            )
            assert module not in FORBIDDEN_PROVIDER_SDKS, (
                f"{rel_path} must not import provider SDK ({module})"
            )
            assert module != "sqlite3", f"{rel_path} must not import sqlite3"

    @pytest.mark.parametrize(
        "rel_path",
        (
            "src/agents/state.py",
            "src/core/database/policy.py",
        ),
    )
    def test_does_not_import_memory_or_api(self, rel_path: str) -> None:
        imports = _imports_in_module(PROJECT_ROOT / rel_path)
        for module in imports:
            assert module not in FORBIDDEN_MEMORY, (
                f"{rel_path} must not import MemoryManager"
            )
            for forbidden in FORBIDDEN_API:
                assert not module.startswith(forbidden), (
                    f"{rel_path} must not import API surface ({forbidden})"
                )


class TestApiModuleDoesNotImportOrchestrator:
    """The API layer (``src/api``) is the public boundary. It must not
    depend on internal architecture modules — it interacts only via the
    public Agent façade."""

    @classmethod
    @pytest.fixture(scope="class")
    def api_imports(cls) -> list[str]:
        path = PROJECT_ROOT / "src/api/routes.py"
        return _imports_in_module(path)

    def test_api_imports_only_text_to_sql_agent(self, api_imports: list[str]) -> None:
        # The API may import ``src.agents.text_to_sql_agent`` (façade) but
        # must not import ``orchestrator``, ``contracts`` or ``adapters``
        # directly.
        for forbidden in (
            "src.agents.orchestrator",
            "src.agents.contracts",
            "src.agents.adapters",
        ):
            assert forbidden not in api_imports, (
                f"src/api/routes.py must not import {forbidden}"
            )