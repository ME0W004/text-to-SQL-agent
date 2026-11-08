# tests/test_llm/conftest.py

"""Shared deterministic fakes for the Sprint 3B LLM test suites.

No test in this package performs a live provider call: providers are
fakes implementing the canonical ``BaseLLMClient`` interface, and
router telemetry is captured by patching ``log_event`` inside the
router module. Provider SDKs may be imported here for exception-mapping
tests only — never to reach a real service.
"""

from __future__ import annotations

from typing import Optional

import pytest

from src.core.llm.base import BaseLLMClient


class FakeProvider(BaseLLMClient):
    """Scriptable provider fake: each call consumes the next script item.

    Script items are response strings or exception instances to raise.
    The last item repeats when the script is exhausted, so a single-item
    script exercises "always failing" providers and a two-item script
    exercises recovery on retry.
    """

    def __init__(
        self,
        name: str = "fake-a",
        model: str = "fake-model",
        script=None,
        available: bool = True,
    ) -> None:
        self.name = name
        self.model = model
        self.script = list(script) if script else ["ok"]
        self.available = available
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        return self.available

    def generate(
        self,
        prompt: str,
        *,
        timeout_seconds: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "timeout_seconds": timeout_seconds,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


class RecordingRouter:
    """Fake router for adapter tests: records generate() call kwargs."""

    def __init__(self, text: str = "router-text", order=("fake-a",)) -> None:
        self.text = text
        self.order = tuple(order)
        self.calls: list[dict] = []

    def generate(self, prompt, *, task, policy=None, budget=None):
        self.calls.append(
            {
                "prompt": prompt,
                "task": task,
                "policy": policy,
                "budget": budget,
            }
        )
        return self._result()

    def _result(self):
        from src.core.llm.policy import LLMResult, Usage

        return LLMResult(
            text=self.text, provider="fake-a", model="fake-model", usage=Usage()
        )

    def has_available_provider(self) -> bool:
        return True

    @property
    def provider_order(self):
        return self.order


@pytest.fixture
def router_events(monkeypatch) -> list[dict]:
    """Capture the structured events the router emits.

    Replaces ``log_event`` inside the router module with a recorder so
    assertions can verify event names, ordering, and hygiene (no raw
    prompt content) without depending on log formatting.
    """
    events: list[dict] = []

    def _record(event, *args, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("src.core.llm.router.log_event", _record)
    return events
