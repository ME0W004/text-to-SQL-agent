# tests/test_llm/test_client_adapter.py

"""Task-bound compatibility adapter (Sprint 3B STEP 6).

``LLMClient`` must remain a thin ``generate(prompt) -> str`` adapter over
the router: no provider selection, no fallback logic, no connectivity
ping. The no-argument constructor keeps legacy callers working.
"""

from __future__ import annotations

import pytest

from src.core.llm.client import LLMClient
from src.core.llm.router import LLMRouter

from .conftest import RecordingRouter


class TestTaskBinding:
    def test_generate_delegates_with_bound_task(self):
        router = RecordingRouter(text="answer-text")
        client = LLMClient(router, task="sql_generation")
        assert client.generate("the prompt") == "answer-text"
        assert router.calls == [
            {"prompt": "the prompt", "task": "sql_generation", "policy": None, "budget": None}
        ]

    def test_default_task_is_general(self):
        router = RecordingRouter()
        client = LLMClient(router)
        client.generate("p")
        assert router.calls[0]["task"] == "general"

    def test_two_task_bound_clients_are_independent(self):
        router = RecordingRouter()
        sql_client = LLMClient(router, task="sql_generation")
        answer_client = LLMClient(router, task="response_generation")
        assert sql_client.task != answer_client.task
        sql_client.generate("a")
        answer_client.generate("b")
        assert [c["task"] for c in router.calls] == [
            "sql_generation",
            "response_generation",
        ]

    def test_is_available_delegates_to_router(self):
        class AvailabilityRouter(RecordingRouter):
            def __init__(self, available):
                super().__init__()
                self._available = available

            def has_available_provider(self):
                return self._available

        assert LLMClient(AvailabilityRouter(True)).is_available() is True
        assert LLMClient(AvailabilityRouter(False)).is_available() is False


class TestLegacyConstructor:
    def test_no_argument_constructor_builds_a_default_router(self):
        client = LLMClient()
        assert isinstance(client.router, LLMRouter)
        # Deterministic validity of the configured chain (whatever the
        # environment configures): known provider names only.
        assert set(client.router.provider_order) <= {
            "openai",
            "groq",
            "gemini",
            "ollama",
        }

    def test_generate_prompt_call_shape_preserved(self):
        # scripts/generate_column_map.py relies on exactly this surface.
        client = LLMClient(RecordingRouter(text="sql"))
        assert client.generate("prompt") == "sql"


class TestRemovedPing:
    def test_internet_ping_helper_is_gone(self):
        # The pre-call connectivity ping (and its helper
        # ``is_internet_available``) were removed by the approved 3.0A
        # design; availability is configuration/provider-based now.
        assert not hasattr(LLMClient, "is_internet_available")

    def test_availability_makes_no_network_calls(self, monkeypatch):
        def no_network(*args, **kwargs):  # any socket use fails the test
            raise AssertionError("network access attempted")

        monkeypatch.setattr("socket.socket.connect", no_network)
        client = LLMClient(RecordingRouter())
        assert client.is_available() is True
