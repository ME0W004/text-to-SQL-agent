# tests/test_architecture/test_schema_boundary.py

"""Schema Intelligence boundary enforcement (Sprint 3.1B Phase 23).

Protects the ADR-017 boundaries:

* ``src/core/schema/**`` never imports the database driver, the LLM
  layer, provider SDKs, AgentState, API/UI or memory;
* ``pymssql`` import sites are only the two approved database modules;
* the schema value objects have exactly one canonical definition in
  ``src/agents/contracts.py``;
* importing schema modules in a fresh subprocess stays clean (no driver,
  no SDK, no state, no network-capable module).
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

SCHEMA_PACKAGE = SRC_ROOT / "core" / "schema"

FORBIDDEN_IN_SCHEMA_PACKAGE: tuple[str, ...] = (
    "pymssql",
    "src.core.llm",
    "src.agents.state",
    "src.agents.adapters",
    "src.agents.orchestrator",
    "src.agents.text_to_sql_agent",
    "src.api",
    "src.web",
    "src.memory",
    "fastapi",
    "streamlit",
    "openai",
    "langchain_groq",
    "langchain_ollama",
    "google.genai",
)

# Driver import sites approved by ADR-017 (+ connection.py, ADR-016 era).
APPROVED_DRIVER_SITES = (
    "src/core/database/connection.py",
    "src/core/database/metadata.py",
)

# Value objects that must keep a single canonical definition.
CANONICAL_SCHEMA_VO_NAMES = (
    "ColumnDescriptor",
    "TableDescriptor",
    "Relationship",
    "BusinessSemantics",
    "SchemaVersion",
    "SchemaSnapshot",
)


def _schema_python_files() -> list[pathlib.Path]:
    return sorted(SCHEMA_PACKAGE.rglob("*.py"))


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


class TestSchemaPackageImportRules:
    @pytest.mark.parametrize("path", _schema_python_files(), ids=lambda p: p.name)
    def test_schema_package_forbidden_imports(self, path: pathlib.Path) -> None:
        modules = _imported_modules(path)
        for forbidden in FORBIDDEN_IN_SCHEMA_PACKAGE:
            offenders = [m for m in modules if m == forbidden or m.startswith(forbidden + ".")]
            assert not offenders, (
                f"{path.relative_to(PROJECT_ROOT)} must not import {forbidden!r} "
                f"(found {offenders})"
            )

    @pytest.mark.parametrize("path", _schema_python_files(), ids=lambda p: p.name)
    def test_schema_package_never_imports_the_driver(self, path: pathlib.Path) -> None:
        # Redundant with the matrix above but stated explicitly: the
        # catalog reaches the driver only through the metadata adapter.
        assert "pymssql" not in _imported_modules(path)

    def test_schema_package_never_imports_agents_state_even_transitively(self):
        # The schema package imports only contracts, the metadata adapter
        # and utils.logger; assert the full import list to catch
        # accidental new edges. ``src.agents.contracts`` is the approved
        # canonical contract surface (ADR-012).
        allowed_modules = ("src.agents.contracts",)
        allowed_prefixes = ("src.core.database.metadata", "src.core.schema", "src.utils")
        for path in _schema_python_files():
            for module in _imported_modules(path):
                if module.startswith("src."):
                    assert (
                        module in allowed_modules
                        or any(
                            module == prefix or module.startswith(prefix + ".")
                            for prefix in allowed_prefixes
                        )
                    ), f"{path.name} has unexpected src import: {module}"


class TestDriverImportSites:
    def test_pymssql_sites_are_the_approved_modules(self) -> None:
        offenders = []
        for path in sorted(SRC_ROOT.rglob("*.py")):
            modules = _imported_modules(path)
            if any(m == "pymssql" or m.endswith(".pymssql") for m in modules):
                rel = path.relative_to(PROJECT_ROOT).as_posix()
                if rel not in APPROVED_DRIVER_SITES:
                    offenders.append(rel)
        assert offenders == [], f"unapproved pymssql import sites: {offenders}"


class TestCanonicalOwnership:
    @pytest.mark.parametrize("vo_name", CANONICAL_SCHEMA_VO_NAMES)
    def test_schema_value_object_defined_only_in_contracts(self, vo_name: str) -> None:
        offenders = []
        for path in sorted(SRC_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == vo_name:
                    rel = path.relative_to(PROJECT_ROOT).as_posix()
                    if rel != "src/agents/contracts.py":
                        offenders.append(rel)
        assert offenders == [], (
            f"{vo_name} must be defined only in src/agents/contracts.py; "
            f"found duplicates in {offenders}"
        )


class TestFreshSubprocessFootprint:
    @pytest.mark.parametrize("target", [
        "src.core.schema.catalog",
        "src.core.schema.retriever",
        "src.core.schema.renderer",
        "src.core.database.metadata",
    ])
    def test_import_footprint_stays_clean(self, target: str) -> None:
        code = (
            "import sys\n"
            f"import {target}\n"
            "banned = [m for m in sys.modules if m.split('.')[0] in "
            "('pymssql', 'openai', 'langchain_groq', 'langchain_ollama', 'google', 'requests', 'fastapi', 'streamlit')]\n"
            "state_loaded = any(m == 'src.agents.state' or m.startswith('src.agents.state.') for m in sys.modules)\n"
            "# src.core.llm.policy is the approved provider-neutral VO leaf\n"
            "# (ADR-016 re-export pattern, imported transitively via\n"
            "# src.agents.contracts); provider implementations and boundary\n"
            "# layers must never load.\n"
            "llm_boundary = [m for m in sys.modules if m.startswith('src.core.llm.')\n"
            "                and m != 'src.core.llm.policy']\n"
            "print('BANNED:', banned)\n"
            "print('STATE_LOADED:', state_loaded)\n"
            "print('LLM_BOUNDARY:', llm_boundary)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "BANNED: []" in result.stdout
        assert "STATE_LOADED: False" in result.stdout
        assert "LLM_BOUNDARY: []" in result.stdout


class TestConstructionSafety:
    def test_catalog_constructs_offline_and_reader_is_never_called(self, tmp_path) -> None:
        # Construction reads local files only; the reader must not be
        # touched until an explicit read surface is used.
        class ExplodingReader:
            def read_probe(self):  # pragma: no cover - must not be called
                raise AssertionError("reader must not be called at construction")

            def read_metadata(self):  # pragma: no cover - must not be called
                raise AssertionError("reader must not be called at construction")

        from src.core.schema.catalog import SchemaCatalog

        catalog = SchemaCatalog(
            ExplodingReader(),
            annotations_path=str(tmp_path / "missing.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        assert catalog._current_id is None  # nothing served before a refresh


class TestFullSchemaLeakagePrevention:
    """Phase 3.2 — no unbounded schema text may reach an LLM-facing path.

    The legacy prompt path (``FULL_SCHEMA`` embedded by
    ``src/core/prompts.py``) is the documented transitional dual-schema
    path; these tests pin exactly where it may live so no *new* path can
    silently start importing it, and pin the bounded alternatives.
    """

    def test_database_models_imported_only_by_legacy_prompts(self) -> None:
        # ``src.core.database.models`` (FULL_SCHEMA, static dicts) is the
        # legacy prompt-truth source. The only sanctioned importer is the
        # legacy SQL-generation prompt builder. Any new importer fails here.
        importers = []
        for path in sorted(SRC_ROOT.rglob("*.py")):
            modules = _imported_modules(path)
            if "src.core.database.models" in modules:
                importers.append(path.relative_to(PROJECT_ROOT).as_posix())
        assert importers == ["src/core/prompts.py"], (
            f"src.core.database.models importers outside the documented legacy "
            f"prompt path: {importers}"
        )

    @pytest.mark.parametrize("module", [
        "src/core/schema/retriever.py",
        "src/core/schema/renderer.py",
        "src/agents/orchestrator.py",
    ])
    def test_retrieval_and_rendering_paths_never_reference_full_schema(self, module: str) -> None:
        source = (PROJECT_ROOT / module).read_text(encoding="utf-8")
        assert "FULL_SCHEMA" not in source, (
            f"{module} must never inject the legacy FULL_SCHEMA text"
        )

    def test_renderer_module_is_formatting_only(self) -> None:
        # The renderer imports the contract value objects and nothing
        # else: no retriever (retrieval-free), no catalog, no logger, no
        # state, no LLM surface.
        modules = _imported_modules(SRC_ROOT / "core" / "schema" / "renderer.py")
        src_imports = {m for m in modules if m.startswith("src.")}
        assert src_imports == {"src.agents.contracts"}, (
            f"renderer has unexpected src imports: {sorted(src_imports)}"
        )
        assert not any("retriever" in m for m in modules)

    def test_renderer_rejects_non_context_input(self) -> None:
        from datetime import datetime

        from src.agents.contracts import SchemaSnapshot, SchemaVersion
        from src.core.schema.renderer import SchemaContextRenderer

        snapshot = SchemaSnapshot(
            version=SchemaVersion(id="x", captured_at=datetime(2026, 9, 4), source="t"),
        )
        renderer = SchemaContextRenderer()
        with pytest.raises(Exception) as excinfo:
            renderer.render(snapshot)  # type: ignore[arg-type]
        assert "SchemaContext" in str(excinfo.value)

    def test_llm_boundary_modules_do_not_import_schema_catalog_or_snapshot(self) -> None:
        # LLM-facing infrastructure (the router and its clients) must not
        # pull the canonical schema representation themselves: schema
        # content reaches LLM-facing surfaces only through the rendered
        # bounded context handed to them by the Orchestrator.
        llm_modules = sorted((SRC_ROOT / "core" / "llm").rglob("*.py"))
        forbidden = (
            "src.core.schema.catalog",
            "src.core.schema.retriever",
            "src.core.schema.renderer",
            "src.core.database.models",
        )
        offenders = []
        for path in llm_modules:
            modules = _imported_modules(path)
            if any(m in forbidden for m in modules):
                offenders.append(path.name)
        assert offenders == [], f"LLM modules importing schema sources: {offenders}"
