# src/agents/__init__.py

"""Lazy public export surface for the agent package (PEP 562).

Imports here are deliberately deferred via module-level ``__getattr__``.
Importing a lightweight submodule such as ``src.agents.contracts`` or
``src.agents.state`` must NOT pull in the whole application stack —
previously this package eagerly imported the façade, the Orchestrator and
the adapters, so even the contract module transitively loaded the openai
SDK, pymssql and every application module (ADR-016, finding F1). The
architecture tests in ``tests/test_architecture/test_import_footprint.py``
enforce the lightweight footprint.

All names listed in ``__all__`` keep working via
``from src.agents import <name>`` — they simply load their defining module
on first access. The mapping below is explicit and hand-maintained; there
is no wildcard import, dynamic scanning or importlib magic.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static-analysis aid only
    from src.agents.adapters import QueryExecutorAdapter, SecurityValidatorAdapter
    from src.agents.orchestrator import Orchestrator, OrchestratorDeps
    from src.agents.state import (
        AgentState,
        ConversationContext,
        ErrorCategory,
        ErrorState,
        ExecutionState,
        ExecutionStatus,
        PipelineStage,
        RequestContext,
        ResponseState,
        RuntimeMetadata,
        SQLState,
        ValidationStatus,
        create_initial_state,
        state_to_api_response,
    )
    from src.agents.text_to_sql_agent import TextToSQLAgent

# Public name -> defining module. Same names as the previous eager
# exports; only the import timing changed.
_EXPORTS = {
    "TextToSQLAgent": "src.agents.text_to_sql_agent",
    "AgentState": "src.agents.state",
    "RequestContext": "src.agents.state",
    "ConversationContext": "src.agents.state",
    "SQLState": "src.agents.state",
    "ExecutionState": "src.agents.state",
    "ResponseState": "src.agents.state",
    "ErrorState": "src.agents.state",
    "RuntimeMetadata": "src.agents.state",
    "PipelineStage": "src.agents.state",
    "ErrorCategory": "src.agents.state",
    "ValidationStatus": "src.agents.state",
    "ExecutionStatus": "src.agents.state",
    "create_initial_state": "src.agents.state",
    "state_to_api_response": "src.agents.state",
    "Orchestrator": "src.agents.orchestrator",
    "OrchestratorDeps": "src.agents.orchestrator",
    "SecurityValidatorAdapter": "src.agents.adapters",
    "QueryExecutorAdapter": "src.agents.adapters",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
