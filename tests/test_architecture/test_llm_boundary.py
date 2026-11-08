"""Static LLM-boundary enforcement tests (Sprint 3 / Prompt 3.0B STEP 13).

Makes the Sprint 3A LLM boundary machine-enforced:

* the SQL extraction path does not import the LLM layer at all;
* ``base.py`` / ``errors.py`` / ``policy.py`` stay provider-neutral;
* ``router.py`` has no eager provider-SDK imports (lazy factories only);
* the LLM layer never imports the application package (no package
  cycle — the Sprint 3B regression is permanently excluded);
* provider implementations cannot import upward (state/API/UI/DB/memory);
* the LLM value objects have exactly one canonical definition
  (``src/core/llm/policy.py``, the ADR-016 leaf pattern) and every other
  surface re-exports it.

Runtime import-footprint probes (SDK loads only on provider
construction) live in ``tests/test_llm/test_import_purity.py``.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

LLM_PACKAGE = PROJECT_ROOT / "src" / "core" / "llm"

PROVIDER_SDK_MODULES = (
    "openai",
    "langchain",
    "langchain_groq",
    "langchain_ollama",
    "langchain_core",
    "google.genai",
    "google",
    "requests",
)

PROVIDER_CLIENT_MODULES = (
    "src.core.llm.openai_client",
    "src.core.llm.gemini_client",
    "src.core.llm.groq_client",
    "src.core.llm.ollama_client",
)

FORBIDDEN_UPWARD = (
    "src.agents.state",
    "src.memory",
    "src.api",
    "src.web",
    "src.core.database",
    "pymssql",
    "fastapi",
    "streamlit",
)


def _imports_of(path: pathlib.Path) -> list[str]:
    """All internal/external module references in *path* (any depth)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def _module_level_imports(path: pathlib.Path) -> list[str]:
    """Only the imports executed at module import time (depth 0)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


class TestSqlExtractorIsolation:
    """STEP 8/13.5: SQLExtractor depends on no LLM module at all."""

    def test_extractor_does_not_import_llm_layer(self):
        imports = _imports_of(PROJECT_ROOT / "src/core/sql_extractor/extractor.py")
        for module in imports:
            assert not module.startswith("src.core.llm"), (
                f"SQLExtractor must not import the LLM layer (found {module!r}); "
                "it accepts any generate(prompt) -> str client"
            )
            assert module != "src.core.llm", (
                "SQLExtractor must not import the LLM package"
            )

    def test_extractor_does_not_reference_llmclient(self):
        source = (PROJECT_ROOT / "src/core/sql_extractor/extractor.py").read_text(
            encoding="utf-8"
        )
        assert "LLMClient" not in source, (
            "SQLExtractor must not reference LLMClient (legacy provider facade)"
        )


class TestProviderNeutrality:
    """STEP 13.2/13.3: base, errors and the value-object leaf are pure."""

    @pytest.mark.parametrize(
        "rel_path",
        ["src/core/llm/base.py", "src/core/llm/errors.py", "src/core/llm/policy.py"],
    )
    def test_pure_llm_modules_import_no_provider_sdk(self, rel_path: str):
        for module in _imports_of(PROJECT_ROOT / rel_path):
            for sdk in PROVIDER_SDK_MODULES:
                assert not module.startswith(sdk), (
                    f"{rel_path} must stay provider-neutral; found import {module!r}"
                )

    def test_policy_module_imports_are_minimal(self):
        allowed = {"__future__", "dataclasses", "typing"}
        imports = set(_imports_of(LLM_PACKAGE / "policy.py"))
        unexpected = imports - allowed
        assert unexpected == set(), (
            f"policy.py gained unexpected imports: {sorted(unexpected)}"
        )

    def test_router_has_no_eager_provider_imports(self):
        """STEP 13.4: provider SDK/client imports in ``router.py`` may only
        appear inside lazy factory bodies, never at module import time."""
        eager = _module_level_imports(LLM_PACKAGE / "router.py")
        for module in eager:
            for forbidden in (*PROVIDER_SDK_MODULES, *PROVIDER_CLIENT_MODULES):
                assert not module.startswith(forbidden), (
                    f"router.py eagerly imports {module!r}; provider imports "
                    "must stay inside the lazy factory bodies"
                )


class TestNoPackageCycle:
    """The Sprint 3B regression (``src.agents ↔ src.core.llm``) is
    permanently excluded: no module of the LLM layer may import the
    application package. The application-facing contract types live in
    the re-export-free leaf ``policy.py`` (ADR-016 pattern)."""

    def test_llm_layer_never_imports_agents_package(self):
        for path in sorted(LLM_PACKAGE.rglob("*.py")):
            for module in _imports_of(path):
                assert not module.startswith("src.agents"), (
                    f"{path.relative_to(PROJECT_ROOT)} imports {module!r}; the "
                    "LLM layer must not depend on src.agents (package cycle)"
                )


class TestProviderUpwardImports:
    """STEP 13.7: provider implementations cannot import upward."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "src/core/llm/openai_client.py",
            "src/core/llm/groq_client.py",
            "src/core/llm/gemini_client.py",
            "src/core/llm/ollama_client.py",
        ],
    )
    def test_provider_client_imports_nothing_upward(self, rel_path: str):
        for module in _imports_of(PROJECT_ROOT / rel_path):
            for forbidden in FORBIDDEN_UPWARD:
                assert not module.startswith(forbidden), (
                    f"{rel_path} must not import {forbidden} (upward dependency)"
                )

    def test_all_providers_subclass_canonical_base(self):
        from src.core.llm.base import BaseLLMClient
        from src.core.llm.gemini_client import GeminiClient
        from src.core.llm.groq_client import GroqClient
        from src.core.llm.ollama_client import OllamaClient
        from src.core.llm.openai_client import OpenAIClient

        for cls in (OpenAIClient, GroqClient, GeminiClient, OllamaClient):
            assert issubclass(cls, BaseLLMClient), f"{cls.__name__} must subclass BaseLLMClient"


class TestCanonicalOwnership:
    """LLM value objects: one canonical definition, re-exported surfaces
    (mirror of the ``src.core.database.policy`` enforcement, ADR-016)."""

    def test_contracts_reexports_canonical_objects(self):
        from src.agents import contracts
        from src.core.llm import policy

        assert contracts.Usage is policy.Usage
        assert contracts.LLMPolicy is policy.LLMPolicy
        assert contracts.LLMBudget is policy.LLMBudget
        assert contracts.LLMResult is policy.LLMResult

    def test_definitions_live_only_in_policy_leaf(self):
        CANONICAL = "src/core/llm/policy.py"
        owned_names = {"Usage", "LLMPolicy", "LLMBudget", "LLMResult"}
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
            "duplicate definitions of the LLM value objects found "
            f"(canonical home is {CANONICAL}): {offenders}"
        )


class TestFacadeBoundary:
    """STEP 13.6/13.11: the facade may wire the compatibility adapter but
    never a provider implementation; the F3 openai-SDK transitive load is
    removed (runtime probes in tests/test_llm/test_import_purity.py)."""

    def test_facade_imports_no_provider_implementation(self):
        imports = _imports_of(PROJECT_ROOT / "src/agents/text_to_sql_agent.py")
        for module in imports:
            for forbidden in PROVIDER_CLIENT_MODULES:
                assert not module.startswith(forbidden), (
                    f"TextToSQLAgent must not import {forbidden}; it wires the "
                    "LLMRouter via the compatibility adapter"
                )
