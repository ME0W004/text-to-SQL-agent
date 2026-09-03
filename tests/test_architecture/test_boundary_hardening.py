"""Boundary hardening tests (Sprint 2 Prompt 3C).

Stage 3B made the lightweight agent import surface real; this module
makes the surrounding boundaries *enforceable against the whole source
tree*, so a future module in the wrong place fails automatically:

* **Whole-tree SDK boundaries** — ``pymssql`` may only be imported by
  the DB driver adapter, the OpenAI SDK only by the LLM provider layer,
  ``fastapi`` only by the API layer, ``streamlit`` only by the UI layer
  and ``sqlite3`` only by the memory implementation (ADR-016).
* **Whole-tree façade / memory direction** — only the API layer (and
  the lazy package export mapping) may import the
  ``TextToSQLAgent`` façade; only the façade may import the memory
  implementation.
* **Import-time side effects** — importing the protected lightweight
  surfaces in a fresh interpreter must not open sockets, spawn
  processes, write files, configure logging handlers, or construct
  state/service objects.
* **Global state** — no module-level ``AgentState`` /
  ``DatabaseConnection`` instances may appear through package
  restructuring (single-writer rule, ADR-013).

The side-effect probes are behavioral (CPython audit hooks + a
before/after working-directory diff in a fresh subprocess), not mocked.
"""

from __future__ import annotations

import ast
import functools
import json
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

# Surfaces whose import must be free of side effects (ADR-016
# lightweight surface + the lazy database package export).
SIDE_EFFECT_TARGETS = (
    "src.agents.contracts",
    "src.agents.state",
    "src.core.database.policy",
    "src.core.database",
)


# ---------------------------------------------------------------------------
# Whole-tree AST helpers
# ---------------------------------------------------------------------------


def _src_python_files() -> list[pathlib.Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _imports_of(path: pathlib.Path) -> list[str]:
    """Every absolute module reference imported by *path* (AST-level,
    including ``TYPE_CHECKING``-guarded imports — a guarded import of a
    forbidden module is still a dependency smell and is rejected)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative imports are internal-only; covered elsewhere
            found.append(node.module or "")
    return found


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _offenders(
    forbidden: str | tuple[str, ...],
    allowed: tuple[str, ...],
    *,
    prefix_match: bool = False,
) -> list[str]:
    """Return ``rel_path: module`` pairs violating an import boundary.

    *forbidden* is a module name or tuple of names; a file under *src*
    that imports one of them and is not listed in *allowed* is an
    offender. With ``prefix_match=True`` the import matches when it
    equals the forbidden name or starts with ``name + "."``.
    """
    names = (forbidden,) if isinstance(forbidden, str) else forbidden

    def matches(module: str) -> bool:
        return any(
            module == n or (prefix_match and module.startswith(n + "."))
            for n in names
        )

    offenders: list[str] = []
    for path in _src_python_files():
        rel = _rel(path)
        if any(rel == a or rel.startswith(a.rstrip("/") + "/") for a in allowed):
            continue
        for module in _imports_of(path):
            if matches(module):
                offenders.append(f"{rel}: {module}")
    return offenders


# ---------------------------------------------------------------------------
# Whole-tree SDK / layer boundaries (ADR-016)
# ---------------------------------------------------------------------------


class TestWholeTreeSdkBoundaries:
    """Heavy third-party SDKs may only enter through their designated
    adapter layer. These scans cover every file under ``src/``, so a new
    module placed in the wrong layer fails immediately."""

    def test_pymssql_only_imported_by_database_driver_adapter(self) -> None:
        offenders = _offenders("pymssql", allowed=("src/core/database/connection.py",))
        assert offenders == [], (
            "pymssql is only allowed in the DB driver adapter "
            f"(src/core/database/connection.py): {offenders}"
        )

    def test_openai_sdk_only_imported_by_llm_provider_layer(self) -> None:
        offenders = _offenders("openai", allowed=("src/core/llm/",))
        assert offenders == [], (
            f"the openai SDK is only allowed in src/core/llm/: {offenders}"
        )

    def test_fastapi_only_imported_by_api_layer(self) -> None:
        offenders = _offenders("fastapi", allowed=("src/api/",))
        assert offenders == [], f"fastapi is only allowed in src/api/: {offenders}"

    def test_streamlit_only_imported_by_ui_layer(self) -> None:
        offenders = _offenders("streamlit", allowed=("src/web/",))
        assert offenders == [], f"streamlit is only allowed in src/web/: {offenders}"

    def test_sqlite3_only_imported_by_memory_implementation(self) -> None:
        offenders = _offenders("sqlite3", allowed=("src/memory/",))
        assert offenders == [], (
            f"sqlite3 is only allowed in the memory implementation: {offenders}"
        )


class TestWholeTreeInternalBoundaries:
    """Internal implementation packages must only be reached through
    their sanctioned entrypoints (dependency-rules.md)."""

    def test_memory_implementation_only_imported_by_facade(self) -> None:
        offenders = _offenders(
            "src.memory",
            allowed=("src/memory/", "src/agents/text_to_sql_agent.py"),
            prefix_match=True,
        )
        assert offenders == [], (
            "src.memory.* may only be imported by the façade "
            f"(src/agents/text_to_sql_agent.py) or the memory package: {offenders}"
        )

    def test_facade_only_imported_by_api_layer_and_package_exports(self) -> None:
        offenders = _offenders(
            "src.agents.text_to_sql_agent",
            allowed=(
                "src/agents/text_to_sql_agent.py",
                "src/agents/__init__.py",  # TYPE_CHECKING + lazy _EXPORTS mapping
                "src/api/",  # the API talks only to the façade
            ),
            prefix_match=True,
        )
        assert offenders == [], (
            "the TextToSQLAgent façade may only be imported by the API "
            f"layer and the lazy package exports: {offenders}"
        )

    def test_contracts_state_policy_do_not_import_facade(self) -> None:
        """The lightweight contract/state surface must not depend on the
        application façade (downward-only, ADR-016)."""
        for rel in (
            "src/agents/contracts.py",
            "src/agents/state.py",
            "src/core/database/policy.py",
            "src/agents/orchestrator.py",
            "src/agents/adapters.py",
        ):
            imports = _imports_of(PROJECT_ROOT / rel)
            assert "src.agents.text_to_sql_agent" not in imports, (
                f"{rel} must not import the façade"
            )


# ---------------------------------------------------------------------------
# Import-time side effects (fresh subprocess + CPython audit hooks)
# ---------------------------------------------------------------------------

_SIDE_EFFECT_PROBE = r"""
import json, os, sys

target = "__TARGET__"

violations = []
_NETWORK_EVENTS = {
    "socket.connect", "socket.getaddrinfo", "socket.bind", "socket.sendto",
    "urllib.Request", "subprocess.Popen", "os.system", "os.exec", "os.spawn",
}

def _audit(event, args):
    if event in _NETWORK_EVENTS:
        violations.append([event, str(args[0])[:120]])
    elif event == "open" and isinstance(args[0], (str, bytes)):
        path = os.fsdecode(args[0])
        mode, flags = args[1], args[2]
        if "__pycache__" in path or path.endswith(".pyc"):
            return
        writeish = (
            (mode is not None and any(c in mode for c in "wax+"))
            or (
                mode is None and flags is not None and (
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                             os.O_APPEND | os.O_TRUNC)
                )
            )
        )
        if writeish:
            violations.append(["open-for-write", path[:120]])

sys.addaudithook(_audit)

import logging as _logging
handlers_before = len(_logging.getLogger().handlers)

def _snapshot(root):
    found = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            found.add(os.path.join(os.path.relpath(dirpath, root), name))
    return found

root = os.getcwd()
before = _snapshot(root)

__IMPORT_TARGET__

after = _snapshot(root)
new_files = sorted(after - before)

# No live service/state objects may be constructed at import time.
import src.agents.state as _state_mod
live = []
for name, value in vars(_state_mod).items():
    if isinstance(value, _state_mod.AgentState):
        live.append("src.agents.state." + name)

handlers = len(__import__("logging").getLogger().handlers)

print(json.dumps({
    "violations": violations,
    "new_files": new_files,
    "live_state": live,
    "root_log_handlers_added": max(0, handlers - handlers_before),
}))
"""


@functools.lru_cache(maxsize=None)
def _run_side_effect_probe(target: str) -> dict:
    # For the database package target, importing only the package must
    # not load the driver adapter; the class-existence scan still works
    # because AgentState's module is imported by the probe itself.
    script = _SIDE_EFFECT_PROBE.replace(
        "__TARGET__", target
    ).replace(
        "__IMPORT_TARGET__", "import " + target
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=PROJECT_ROOT, check=True, timeout=120,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestImportTimeSideEffects:
    """Importing the protected surfaces must not open sockets, execute
    SQL via the driver, spawn processes, write files, configure root
    logging handlers or construct AgentState instances.

    These are behavioral checks (CPython audit hooks + filesystem diff
    in a fresh interpreter), so they detect side effects no static AST
    scan can see. Legitimate static initialization — module-level
    constants, dataclass/enum definitions, lazy logging *functions* —
    is untouched: logging is configured lazily on the first
    ``get_logger()`` call, not at import.
    """

    @pytest.mark.parametrize("target", SIDE_EFFECT_TARGETS)
    def test_import_has_no_runtime_side_effects(self, target: str) -> None:
        probe = _run_side_effect_probe(target)
        assert probe["violations"] == [], (
            f"importing {target} performed runtime side effects: "
            f"{probe['violations']}"
        )

    @pytest.mark.parametrize("target", SIDE_EFFECT_TARGETS)
    def test_import_writes_no_files(self, target: str) -> None:
        probe = _run_side_effect_probe(target)
        assert probe["new_files"] == [], (
            f"importing {target} wrote files to disk: {probe['new_files']}"
        )

    @pytest.mark.parametrize("target", SIDE_EFFECT_TARGETS)
    def test_import_does_not_configure_logging_or_state(
        self, target: str
    ) -> None:
        probe = _run_side_effect_probe(target)
        assert probe["root_log_handlers_added"] == 0, (
            f"importing {target} configured root logging handlers at import time"
        )
        assert probe["live_state"] == [], (
            f"importing {target} constructed live AgentState instances: "
            f"{probe['live_state']}"
        )


# ---------------------------------------------------------------------------
# Global state / singleton boundaries
# ---------------------------------------------------------------------------


class TestGlobalStateBoundaries:
    """Package restructuring must not introduce shared mutable state:
    no module-level AgentState, no eager DatabaseConnection singleton,
    no mutable module-level request data."""

    def test_database_package_does_not_instantiate_connection(self) -> None:
        import src.core.database as pkg

        from src.core.database.connection import DatabaseConnection

        for name, value in vars(pkg).items():
            if name.startswith("__"):
                continue
            assert not isinstance(value, DatabaseConnection), (
                f"src.core.database.{name} is a live DatabaseConnection "
                "(shared connection singleton is forbidden)"
            )

    def test_agents_package_holds_only_classes_functions_and_modules(
        self,
    ) -> None:
        """The lazy export surface must not grow module-level mutable
        request data — only classes, functions, the static ``_EXPORTS``
        mapping and (after any submodule import) the submodule bindings
        Python's import machinery sets on the parent package are
        allowed. No live instances, no shared mutable state."""
        import inspect
        import src.agents as pkg

        allowed_static = {"_EXPORTS", "TYPE_CHECKING"}
        for name, value in vars(pkg).items():
            if name.startswith("__") or name in allowed_static:
                continue
            ok = (
                inspect.isclass(value)
                or inspect.isfunction(value)
                or inspect.ismodule(value)
            )
            assert ok, (
                f"src.agents.{name} is {type(value)!r}; the package export "
                "surface must only hold classes/functions/submodules (no "
                "shared mutable instances)"
            )

    def test_state_module_holds_no_prebuilt_state(self) -> None:
        """``src.agents.state`` must not carry module-level AgentState
        instances (every request builds its own via
        ``create_initial_state``)."""
        import src.agents.state as state_mod

        for name, value in vars(state_mod).items():
            assert not isinstance(value, state_mod.AgentState), (
                f"src.agents.state.{name} is a live AgentState instance — "
                "state must be created per request, never shared"
            )
