"""Single-writer rule tests.

Sprint 2 Prompt 2B / Step 2B.33 enforces that only the Orchestrator
writes ``AgentState``. This module verifies:

* No future component module imports ``AgentState`` or its sub-models.
* The state-applier methods on ``AgentState`` are only invoked from
  within ``src/agents/orchestrator.py`` (verified by static AST
  inspection).

Note: ``AgentState`` itself lives in ``src/agents/state.py``. The state
*model* is allowed to import ``AgentState`` (it defines it). The
``Orchestrator`` is allowed to import it (single writer). All other
component modules must not.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Iterable

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_AGENTS = PROJECT_ROOT / "src" / "agents"


# Modules that are allowed to import AgentState / sub-models.
ALLOWED_IMPORTERS = {
    "src/agents/state.py",  # defines AgentState
    "src/agents/orchestrator.py",  # sole writer (ADR-013)
    "src/agents/text_to_sql_agent.py",  # façade that returns state via API
}

# Future component modules that must not import AgentState.
FUTURE_COMPONENT_MODULES = (
    "src/agents/contracts.py",
    "src/agents/adapters.py",
)


def _collect_imports(path: pathlib.Path) -> list[ast.stmt]:
    source = path.read_text(encoding="utf-8")
    return ast.parse(source).body


def _extract_import_targets(body: Iterable[ast.stmt]) -> list[str]:
    targets: list[str] = []
    for stmt in body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                targets.append(alias.name)
        elif isinstance(stmt, ast.ImportFrom):
            module = stmt.module or ""
            if stmt.level:
                # relative import — append module
                targets.append(module)
            else:
                targets.append(module)
    return targets


@pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
def test_future_component_does_not_import_agent_state(rel_path: str) -> None:
    """Future component modules must not import ``src.agents.state``."""
    path = PROJECT_ROOT / rel_path
    targets = _extract_import_targets(_collect_imports(path))
    assert "src.agents.state" not in targets, (
        f"{rel_path} imports AgentState — components must be "
        "single-responsibility and write through the Orchestrator only."
    )


def test_orchestrator_is_the_only_state_writer_in_new_modules() -> None:
    """Only ``orchestrator.py`` calls ``state.with_*`` methods among the
    new modules. We inspect every ``Call`` in the AST and look for
    ``with_*`` attribute calls whose target is an ``AgentState``
    instance.
    """

    def _find_with_calls(path: pathlib.Path) -> list[ast.Call]:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        result: list[ast.Call] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr.startswith("with_"):
                    result.append(node)
        return result

    for rel_path in FUTURE_COMPONENT_MODULES + ("src/agents/orchestrator.py",):
        path = PROJECT_ROOT / rel_path
        calls = _find_with_calls(path)
        if rel_path == "src/agents/orchestrator.py":
            # Expected to use ``state.with_*`` methods — sole writer.
            assert len(calls) >= 1, (
                "Orchestrator must use at least one state.with_* method"
            )
        else:
            assert calls == [], (
                f"{rel_path} calls a with_* method; only Orchestrator may do so"
            )


def test_state_module_exposes_all_required_appliers() -> None:
    """``AgentState`` must expose the state-applier methods introduced
    in Sprint 2 Prompt 2B."""
    from src.agents.state import AgentState

    expected = (
        "with_question_analysis",
        "with_schema_context",
        "with_query_plan",
        "with_result_profile",
        "with_presentation_plan",
        "with_validated_answer",
    )
    for name in expected:
        assert hasattr(AgentState, name), (
            f"AgentState must expose {name} state-applier method"
        )
        assert callable(getattr(AgentState, name))


def test_security_decision_is_typed_correctly() -> None:
    """``SQLState.security_decision`` must be ``Optional[SecurityDecision]``,
    not ``Any``."""
    from src.agents.state import SQLState
    from src.core.security.sql_security import SecurityDecision

    field_types = {f.name: f.type for f in SQLState.__dataclass_fields__.values()}
    assert "security_decision" in field_types
    # The field is typed via ``Optional[SecurityDecision]``; we verify it
    # is NOT plain Any and it references SecurityDecision in its
    # annotation string.
    annotation = field_types["security_decision"]
    assert annotation is not type(Any), (
        "security_decision must NOT be plain Any — use Optional[SecurityDecision]"
    )
    # In practice this is ``Optional[SecurityDecision]`` rendered as
    # ``typing.Optional[SecurityDecision]`` under ``from __future__``.
    assert "SecurityDecision" in str(annotation)


import typing as _t  # noqa: E402  (placed here for fixture use)
Any = _t.Any


# ---------------------------------------------------------------------------
# Whole-tree single-writer enforcement (Sprint 2 Prompt 3C)
# ---------------------------------------------------------------------------


def _agent_state_importers(path: pathlib.Path) -> list[tuple[str, bool]]:
    """Return ``(module, type_checking_guarded)`` pairs for every import
    of ``src.agents.state`` in *path*. Imports nested under an
    ``if TYPE_CHECKING:`` block are flagged as guarded (they are erased
    at runtime and cannot create a real write route)."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            names = set()
            for sub in ast.walk(test):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    names.add(sub.attr)
            if "TYPE_CHECKING" in names:
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        guarded_ids.add(id(sub))
    found: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src.agents.state":
                    found.append((alias.name, id(node) in guarded_ids))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "src.agents.state" or (
                node.module == "src.agents"
                and any(a.name == "state" for a in node.names)
            ):
                found.append((node.module or "src.agents", id(node) in guarded_ids))
    return found


# Runtime importers of AgentState. The orchestrator is the sole writer
# (ADR-013); the façade consumes the final state for the API response;
# state.py defines the model itself.
ALLOWED_RUNTIME_IMPORTERS = {
    "src/agents/state.py",
    "src/agents/orchestrator.py",
    "src/agents/text_to_sql_agent.py",
}

# TYPE_CHECKING-guarded imports are acceptable only in the lazy package
# export surface (they never execute at runtime, and the footprint tests
# prove the package import stays lightweight).
ALLOWED_GUARDED_IMPORTERS = {
    "src/agents/__init__.py",
}


def test_no_production_module_imports_agent_state_outside_the_single_writer() -> None:
    """Stage 3C whole-tree scan: no module under ``src/`` may import
    ``AgentState`` (runtime) outside the approved writer/façade set —
    this protects the ADR-013 single-writer rule against *future*
    modules, not only the current contracts/adapters pair."""
    offenders: list[str] = []
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for module, guarded in _agent_state_importers(path):
            if guarded and rel in ALLOWED_GUARDED_IMPORTERS:
                continue
            if not guarded and rel in ALLOWED_RUNTIME_IMPORTERS:
                continue
            kind = "TYPE_CHECKING" if guarded else "runtime"
            offenders.append(f"{rel} ({kind} import of {module})")
    assert offenders == [], (
        "AgentState imports outside the single-writer boundary "
        f"(ADR-013): {offenders}"
    )