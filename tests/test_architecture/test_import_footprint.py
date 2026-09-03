"""Import-footprint and canonical-ownership tests (Sprint 2 Prompt 3B).

Enforces the ADR-016 decision that the lightweight agent modules
(``contracts``, ``state``) can be imported without pulling in the whole
application stack, and that the database policy value objects have
exactly one canonical definition.

Footprint tests run in fresh subprocesses so that previously imported
modules in the pytest process cannot contaminate the result.
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

# External / application modules that must NOT load when the lightweight
# agent modules are imported (ADR-016, findings F1/F2/F3).
FORBIDDEN_FOOTPRINT = (
    "openai",
    "pymssql",
    "fastapi",
    "streamlit",
    "langchain",
    "requests",
    "dotenv",
    "pydantic",
    "src.memory.manager",
    "src.agents.text_to_sql_agent",
)

# Modules whose import is verified to stay lightweight.
LIGHTWEIGHT_MODULES = (
    "src.agents.contracts",
    "src.agents.state",
    "src.core.database.policy",
)

_PROBE_SCRIPT = """
import json, sys
import {module}
heavy = sorted(m for m in sys.modules if m in {forbidden!r} or
               any(m == p or m.startswith(p + ".") for p in {pkg_prefixes!r}))
src_modules = sorted(m for m in sys.modules if m.startswith("src."))
print(json.dumps({{"heavy": heavy, "src_modules": src_modules}}))
"""

# Packages matched by prefix in the probe (SDK/namespaced distributions).
_PKG_PREFIXES = ("langchain", "openai", "google.genai")


# Cached: each protected module is probed at most once per test run
# (Stage 3C — avoids redundant subprocess probes across the parametrized
# assertions).
@functools.lru_cache(maxsize=None)
def _probe(module: str) -> dict:
    script = _PROBE_SCRIPT.format(
        module=module,
        forbidden=list(FORBIDDEN_FOOTPRINT),
        pkg_prefixes=list(_PKG_PREFIXES),
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=True,
        timeout=120,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


class TestImportFootprint:
    """Importing lightweight agent modules must not load the heavy
    application stack (driver, provider SDKs, UI, memory, façade)."""

    @pytest.mark.parametrize("module", LIGHTWEIGHT_MODULES)
    def test_lightweight_module_does_not_load_heavy_stack(self, module: str) -> None:
        probe = _probe(module)
        assert probe["heavy"] == [], (
            f"importing {module} loaded heavy modules: {probe['heavy']}"
        )

    @pytest.mark.parametrize("module", LIGHTWEIGHT_MODULES)
    def test_lightweight_module_does_not_load_the_facade(self, module: str) -> None:
        probe = _probe(module)
        assert "src.agents.text_to_sql_agent" not in probe["src_modules"]
        assert "src.agents.orchestrator" not in probe["src_modules"]
        assert "src.agents.adapters" not in probe["src_modules"]

    def test_contracts_does_not_load_database_driver_module(self) -> None:
        probe = _probe("src.agents.contracts")
        assert "src.core.database.connection" not in probe["src_modules"], (
            "contracts.py must import policy.py, not connection.py (ADR-016)"
        )

    def test_database_package_import_stays_lazy(self) -> None:
        """Importing the ``src.core.database`` package itself must not
        load the driver adapter or any heavy stack (lazy PEP-562
        exports, ADR-016)."""
        probe = _probe("src.core.database")
        assert probe["heavy"] == [], (
            f"importing src.core.database loaded heavy modules: {probe['heavy']}"
        )
        assert "src.core.database.connection" not in probe["src_modules"], (
            "importing src.core.database must not load the pymssql "
            "adapter module"
        )


class TestFacadeReExportCompatibility:
    """``from src.agents import TextToSQLAgent`` keeps working after the
    package became lazy, and yields the exact façade class (no
    duplicate definition)."""

    def test_facade_importable_through_package_and_identical(self) -> None:
        script = (
            "import json\n"
            "from src.agents import TextToSQLAgent as Reexported\n"
            "from src.agents.text_to_sql_agent import TextToSQLAgent as Direct\n"
            "print(json.dumps({'identical': Reexported is Direct,\n"
            "                  'module': Reexported.__module__}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=True,
            timeout=120,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["identical"] is True
        assert payload["module"] == "src.agents.text_to_sql_agent"


class TestLazyPackageExports:
    """The lazy PEP-562 export surfaces still expose the previously
    eager public names, and they resolve to the same objects as a direct
    module import (no duplicates)."""

    def test_agents_lazy_exports_match_defining_modules(self) -> None:
        import importlib

        import src.agents as pkg

        for name in pkg.__all__:
            value = getattr(pkg, name)
            module = importlib.import_module(pkg._EXPORTS[name])
            assert getattr(module, name) is value, (
                f"src.agents.{name} is not the object defined in "
                f"{pkg._EXPORTS[name]}"
            )

    def test_agents_dir_includes_all_exports(self) -> None:
        import src.agents as pkg

        listing = dir(pkg)
        for name in pkg.__all__:
            assert name in listing

    def test_database_lazy_exports_match_defining_modules(self) -> None:
        import importlib

        import src.core.database as pkg

        for name in pkg.__all__:
            value = getattr(pkg, name)
            module = importlib.import_module(pkg._EXPORTS[name])
            assert getattr(module, name) is value, (
                f"src.core.database.{name} is not the object defined in "
                f"{pkg._EXPORTS[name]}"
            )

    def test_unknown_attribute_still_raises(self) -> None:
        import src.agents as pkg

        with pytest.raises(AttributeError):
            getattr(pkg, "does_not_exist")


class TestCanonicalOwnership:
    """The moved value objects must have exactly one definition
    (``src.core.database.policy``) and every other path must be a
    re-export of that same object (ADR-016)."""

    def test_policy_and_connection_names_are_identical(self) -> None:
        from src.core.database import connection, policy

        assert policy.ExecutionPolicy is connection.ExecutionPolicy
        assert policy.DatabaseError is connection.DatabaseError
        assert policy.DatabaseErrorCode is connection.DatabaseErrorCode

    def test_contracts_uses_canonical_policy_objects(self) -> None:
        from src.agents import contracts
        from src.core.database import policy

        assert contracts.ExecutionPolicy is policy.ExecutionPolicy
        assert contracts.DatabaseErrorCode is policy.DatabaseErrorCode

    def test_definitions_live_only_in_policy(self) -> None:
        """No module under ``src/`` may define the moved types; every
        other path must re-export, not duplicate (Stage 3C: the scan
        covers the whole source tree, so a new duplicate in any future
        module fails)."""
        CANONICAL = "src/core/database/policy.py"
        owned_names = {"ExecutionPolicy", "DatabaseError", "DatabaseErrorCode"}
        offenders: list[str] = []
        for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
            rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            if rel == CANONICAL:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in owned_names:
                    offenders.append(f"{rel}: class {node.name}")
        assert offenders == [], (
            "duplicate definitions of the policy value objects found "
            f"(canonical home is {CANONICAL}): {offenders}"
        )


class TestPolicyModulePurity:
    """``policy.py`` is the driver-free home of the execution policy
    value objects and must never gain infra imports."""

    @classmethod
    @pytest.fixture(scope="class")
    def imports(cls) -> list[str]:
        path = PROJECT_ROOT / "src" / "core" / "database" / "policy.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.append(node.module or "")
        return found

    FORBIDDEN = (
        "pymssql",
        "fastapi",
        "streamlit",
        "sqlite3",
        "src.core.database.connection",
        "src.core.llm",
        "src.memory",
        "src.agents",
        "src.api",
        "src.web",
        "evaluation",
    )

    def test_policy_is_driver_free(self, imports: list[str]) -> None:
        for module in imports:
            for forbidden in self.FORBIDDEN:
                assert not module.startswith(forbidden), (
                    f"policy.py must not import {forbidden} (got {module!r})"
                )

    def test_policy_imports_are_minimal(self, imports: list[str]) -> None:
        allowed = {"__future__", "os", "dataclasses", "enum",
                   "src.utils.exceptions"}
        unexpected = set(imports) - allowed
        assert unexpected == set(), (
            f"policy.py gained unexpected imports: {sorted(unexpected)}"
        )
