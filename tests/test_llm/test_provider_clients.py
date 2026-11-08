# tests/test_llm/test_provider_clients.py

"""Provider client conformance (Sprint 3B STEP 4).

All four provider implementations must conform to the canonical
``BaseLLMClient`` interface, expose metadata, raise provider-neutral
typed errors, and keep SDK exceptions from escaping. Network-touching
SDK calls are replaced by patches; no test contacts a live service.
"""

from __future__ import annotations

import pytest

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import (
    LLMConfigurationError,
    LLMProviderError,
    LLMProviderRateLimited,
    LLMProviderTimeout,
    LLMProviderUnavailable,
)

httpx = pytest.importorskip("httpx")
openai = pytest.importorskip("openai")


def _http_response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://unit.test"))


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------


class TestBaseInterface:
    def test_all_providers_subclass_base(self):
        from src.core.llm.gemini_client import GeminiClient
        from src.core.llm.groq_client import GroqClient
        from src.core.llm.ollama_client import OllamaClient
        from src.core.llm.openai_client import OpenAIClient

        for cls in (OpenAIClient, GroqClient, GeminiClient, OllamaClient):
            assert issubclass(cls, BaseLLMClient)

    def test_base_is_abstract_with_minimal_surface(self):
        with pytest.raises(TypeError):
            BaseLLMClient()  # type: ignore[abstract]
        assert BaseLLMClient.__abstractmethods__ == frozenset(
            {"generate", "is_available"}
        )


class TestSignatureParity:
    """3.0C.10: conformance by signature, not merely by
    ``runtime_checkable`` method presence."""

    def test_provider_generate_signatures_match_base(self):
        import inspect

        from src.core.llm.gemini_client import GeminiClient
        from src.core.llm.groq_client import GroqClient
        from src.core.llm.ollama_client import OllamaClient
        from src.core.llm.openai_client import OpenAIClient

        base = inspect.signature(BaseLLMClient.generate)
        for cls in (OpenAIClient, GroqClient, GeminiClient, OllamaClient):
            sig = inspect.signature(cls.generate)
            assert list(sig.parameters) == list(base.parameters), (
                f"{cls.__name__}.generate signature drifted from "
                "BaseLLMClient.generate"
            )

    def test_error_messages_do_not_embed_api_keys(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-unit-secret-key")
        from src.core.llm.openai_client import OpenAIClient

        client = OpenAIClient(temperature=0.1, max_tokens=500)
        client.client.chat.completions.create = lambda **kw: (
            _ for _ in ()
        ).throw(RuntimeError("boom"))

        with pytest.raises(LLMProviderError) as excinfo:
            client.generate("p")
        assert "sk-unit-secret-key" not in str(excinfo.value)
        assert "sk-unit-secret-key" not in str(excinfo.value.cause)


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------


class TestOpenAIClient:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
        from src.core.llm.openai_client import OpenAIClient

        return OpenAIClient(temperature=0.1, max_tokens=500)

    def test_metadata_and_availability(self, client):
        assert client.name == "openai"
        assert client.model == "gpt-4o-mini"
        assert client.is_available() is True

    def test_unavailable_without_key(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(client, "api_key", None)
        assert client.is_available() is False

    def test_per_request_overrides_and_timeout_reach_the_sdk(self, client):
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})]})()

        client.client.chat.completions.create = fake_create
        text = client.generate("p", timeout_seconds=2.5, temperature=0.0, max_tokens=7)
        assert text == "ok"
        assert captured["timeout"] == 2.5
        assert captured["temperature"] == 0.0
        assert captured["max_tokens"] == 7
        # Legacy defaults preserved when no override is given.
        client.generate("p")
        assert captured["temperature"] == 0.1
        assert captured["max_tokens"] == 500

    def test_sdk_exceptions_are_translated(self, client):
        cases = [
            (
                openai.RateLimitError("rl", response=_http_response(429), body=None),
                LLMProviderRateLimited,
            ),
            (
                openai.APITimeoutError(_http_response(408).request),
                LLMProviderTimeout,
            ),
            (
                openai.AuthenticationError(
                    "bad key", response=_http_response(401), body=None
                ),
                LLMProviderUnavailable,
            ),
            (
                openai.OpenAIError("other api error"),
                LLMProviderError,
            ),
        ]
        for sdk_exc, expected in cases:
            client.client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(sdk_exc)
            with pytest.raises(expected) as excinfo:
                client.generate("p")
            assert excinfo.value.provider == "openai"
            assert excinfo.value.model == client.model
            assert excinfo.value.cause is sdk_exc

    def test_non_sdk_exception_is_normalized(self, client):
        client.client.chat.completions.create = lambda **kw: (_ for _ in ()).throw(
            KeyError("malformed response")
        )
        with pytest.raises(LLMProviderError):
            client.generate("p")


# ---------------------------------------------------------------------------
# GroqClient
# ---------------------------------------------------------------------------


class _FakeRunnable:
    """Stands in for the LangChain runnable (a pydantic model whose
    attributes cannot be monkeypatched directly)."""

    def __init__(self, script=None, overrides=None):
        self.script = list(script) if script else ["ok"]
        self.overrides = overrides if overrides is not None else []
        self.bound: list[dict] = []
        self.invoked: list[str] = []

    def bind(self, **overrides):
        self.bound.append(overrides)
        return self

    def invoke(self, prompt):
        self.invoked.append(prompt)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return type("R", (), {"content": item})()


class TestGroqClient:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "unit-test-key")
        from src.core.llm.groq_client import GroqClient

        return GroqClient()

    def test_metadata_and_availability(self, client):
        assert client.name == "groq"
        assert client.model == "llama-3.3-70b-versatile"
        assert client.is_available() is True

    def test_failures_are_typed_and_cause_preserved(self, client):
        boom = RuntimeError("langchain exploded")
        client.llm = _FakeRunnable(script=[boom])
        with pytest.raises(LLMProviderError) as excinfo:
            client.generate("p")
        assert excinfo.value.provider == "groq"
        assert excinfo.value.cause is boom

    def test_overrides_are_bound_per_call(self, client):
        runnable = _FakeRunnable(script=["ok", "direct"])
        client.llm = runnable
        text = client.generate("p", temperature=0.0, max_tokens=9)
        assert text == "ok"
        assert runnable.bound == [{"temperature": 0.0, "max_tokens": 9}]
        # No overrides → the client is used directly, unbound.
        assert client.generate("p") == "direct"
        assert len(runnable.bound) == 1


# ---------------------------------------------------------------------------
# GeminiClient
# ---------------------------------------------------------------------------


class TestGeminiClient:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "unit-test-key")
        from src.core.llm.gemini_client import GeminiClient

        return GeminiClient()

    def test_metadata_and_availability(self, client):
        assert client.name == "gemini"
        assert client.model.startswith("gemini-")
        assert client.is_available() is True

    def test_timeout_is_translated(self, client):
        from google.genai.errors import httpx as genai_httpx

        def boom(**kwargs):
            raise genai_httpx.TimeoutException("timed out")

        client.client.models.generate_content = boom
        with pytest.raises(LLMProviderTimeout) as excinfo:
            client.generate("p")
        assert excinfo.value.provider == "gemini"

    def test_generic_failure_is_translated(self, client):
        client.client.models.generate_content = lambda **kw: (_ for _ in ()).throw(
            RuntimeError("api exploded")
        )
        with pytest.raises(LLMProviderError) as excinfo:
            client.generate("p")
        assert excinfo.value.provider == "gemini"

    def test_overrides_reach_the_request_config(self, client):
        captured = {}

        def fake_generate_content(**kwargs):
            captured.update(kwargs)
            return type("R", (), {"text": "ok"})()

        client.client.models.generate_content = fake_generate_content
        client.generate("p", timeout_seconds=3.0, temperature=0.0, max_tokens=11)
        config = captured["config"]
        assert config["temperature"] == 0.0
        assert config["max_output_tokens"] == 11
        assert config["http_options"]["timeout"] == 3000

    def test_construction_requires_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        from src.core.llm.gemini_client import GeminiClient

        with pytest.raises(ValueError):
            GeminiClient()


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------


class TestOllamaClient:
    @pytest.fixture
    def client(self):
        from src.core.llm.ollama_client import OllamaClient

        return OllamaClient()

    def test_metadata(self, client):
        assert client.name == "ollama"
        assert client.model == "llama3.1:8b"

    def test_uninitialized_client_is_unavailable_and_typed(self, client):
        client.llm = None  # simulate failed lazy init (no local server)
        assert client.is_available() is False
        with pytest.raises(LLMProviderUnavailable) as excinfo:
            client.generate("p")
        assert excinfo.value.provider == "ollama"

    def test_generation_failure_is_typed(self, client):
        client.llm = type("L", (), {"invoke": staticmethod(lambda p: (_ for _ in ()).throw(RuntimeError("connection refused")))})()
        with pytest.raises(LLMProviderError) as excinfo:
            client.generate("p")
        assert excinfo.value.provider == "ollama"

    def test_unsupported_overrides_do_not_break_generation(self, client):
        invoked = {}
        client.llm = type("L", (), {"invoke": staticmethod(lambda p: invoked.update(p=p) or "ok")})()
        text = client.generate("p", timeout_seconds=1.0, temperature=0.0, max_tokens=5)
        assert text == "ok"
        assert invoked == {"p": "p"}  # wrapper ignores unsupported overrides
