# tests/test_llm/test_errors.py

"""Provider-neutral error taxonomy (Sprint 3B STEP 2).

Verifies the hierarchy, the structured routing context carried by the
errors, and that the module stays provider-neutral (no SDK imports).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.core.llm import errors as llm_errors

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestErrorHierarchy:
    def test_provider_error_is_base_for_all_provider_failures(self):
        assert issubclass(llm_errors.LLMProviderUnavailable, llm_errors.LLMProviderError)
        assert issubclass(llm_errors.LLMProviderTimeout, llm_errors.LLMProviderError)
        assert issubclass(llm_errors.LLMProviderRateLimited, llm_errors.LLMProviderError)

    def test_configuration_error_is_not_a_provider_failure(self):
        # Configuration failures must not be fallback-eligible: the router
        # relies on LLMConfigurationError NOT deriving from LLMProviderError.
        assert not issubclass(llm_errors.LLMConfigurationError, llm_errors.LLMProviderError)
        assert issubclass(llm_errors.LLMConfigurationError, Exception)

    def test_all_taxonomy_names_exported(self):
        for name in (
            "LLMProviderError",
            "LLMProviderUnavailable",
            "LLMProviderTimeout",
            "LLMProviderRateLimited",
            "LLMConfigurationError",
        ):
            assert name in llm_errors.__all__


class TestStructuredContext:
    def test_provider_error_carries_routing_context(self):
        cause = ValueError("boom")
        exc = llm_errors.LLMProviderError(
            "provider blew up",
            provider="openai",
            model="gpt-4o-mini",
            cause=cause,
        )
        assert exc.provider == "openai"
        assert exc.model == "gpt-4o-mini"
        assert exc.cause is cause
        assert exc.__cause__ is None  # set via `raise ... from`, not the ctor

    def test_context_defaults_are_none(self):
        exc = llm_errors.LLMProviderUnavailable("down")
        assert exc.provider is None
        assert exc.model is None
        assert exc.cause is None

    def test_configuration_error_carries_cause(self):
        cause = KeyError("task")
        exc = llm_errors.LLMConfigurationError("bad config", cause=cause)
        assert exc.cause is cause
        assert not hasattr(exc, "provider"), (
            "configuration errors are application failures; they must not "
            "impersonate provider-attributed failures"
        )


class TestProviderNeutrality:
    def test_errors_module_imports_no_provider_sdk(self):
        source = (PROJECT_ROOT / "src/core/llm/errors.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for module in imported:
            for sdk in ("openai", "langchain", "google", "requests", "httpx"):
                assert not module.startswith(sdk), (
                    f"errors.py must stay provider-neutral; found import {module!r}"
                )

    def test_error_messages_do_not_embed_secrets(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret")
        exc = llm_errors.LLMProviderUnavailable("Provider 'openai' is not configured.")
        assert "sk-super-secret" not in str(exc)
