# tests/test_llm/test_router.py

"""LLMRouter behavior (Sprint 3B STEP 5).

Covers protocol conformance, the provider registry, deterministic
selection, bounded classified fallback, error normalization, budget
mapping, result construction, and telemetry hygiene. All providers are
fakes; no live service is contacted.
"""

from __future__ import annotations

import logging
from typing import Optional

import pytest

from src.core.llm.base import BaseLLMClient
from src.core.llm.errors import (
    LLMConfigurationError,
    LLMProviderError,
    LLMProviderRateLimited,
    LLMProviderTimeout,
    LLMProviderUnavailable,
)
from src.core.llm.policy import LLMBudget, LLMPolicy
from src.core.llm.router import LLMRouter, build_default_router

from .conftest import FakeProvider


# ---------------------------------------------------------------------------
# Protocol conformance & construction
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_router_satisfies_canonical_protocol(self):
        from src.agents.contracts import LLMRouter as LLMRouterProtocol

        router = LLMRouter(providers=[FakeProvider()])
        assert isinstance(router, LLMRouterProtocol)

    def test_router_module_defines_no_second_protocol(self):
        # Single contract authority (ADR-012): the router module must
        # implement — not re-declare — the canonical Protocol.
        import src.core.llm.router as router_module
        from typing import Protocol

        protocol_meta = type(Protocol)
        protocols = [
            name
            for name, obj in vars(router_module).items()
            if isinstance(obj, protocol_meta)
        ]
        assert protocols == [], (
            "router module must implement, not re-declare, the canonical "
            f"LLMRouter Protocol (found protocol definitions: {protocols})"
        )

    def test_router_signature_matches_canonical_protocol(self):
        import inspect

        from src.agents.contracts import LLMRouter as LLMRouterProtocol

        proto_sig = inspect.signature(LLMRouterProtocol.generate)
        impl_sig = inspect.signature(LLMRouter.generate)
        assert list(proto_sig.parameters) == list(impl_sig.parameters), (
            "LLMRouter.generate signature drifted from the canonical Protocol"
        )


class TestRegistry:
    def test_injected_providers_are_indexed_by_name_and_reused(self):
        provider = FakeProvider(name="fake-a", script=["one", "two"])
        router = LLMRouter(providers=[provider])
        assert router.provider_order == ("fake-a",)
        first = router.generate("p1", task="t")
        second = router.generate("p2", task="t")
        assert (first.text, second.text) == ("one", "two")
        assert provider.calls[0]["prompt"] == "p1"

    def test_unknown_provider_in_order_fails_fast(self):
        with pytest.raises(LLMConfigurationError) as excinfo:
            LLMRouter(providers=[FakeProvider(name="a")], provider_order=("a", "ghost"))
        assert "ghost" in str(excinfo.value)

    def test_max_attempts_is_bounded_below(self):
        provider = FakeProvider(script=[LLMProviderError("always")])
        router = LLMRouter(providers=[provider], max_attempts_per_provider=0)
        with pytest.raises(LLMProviderError):
            router.generate("p", task="t")
        assert len(provider.calls) == 1  # floored to 1, never 0 or unbounded


# ---------------------------------------------------------------------------
# Deterministic provider selection
# ---------------------------------------------------------------------------


class TestSelection:
    def test_injection_order_is_deterministic(self):
        providers = [FakeProvider(name="b"), FakeProvider(name="a")]
        router = LLMRouter(providers=providers)
        assert router.provider_order == ("b", "a")
        assert router.generate("p", task="t").provider == "b"
        assert router.generate("p", task="t").provider == "b"

    def test_policy_provider_is_tried_first_then_configured_order(self, router_events):
        providers = [
            FakeProvider(name="a", available=False),
            FakeProvider(name="c", available=False),
            FakeProvider(name="b"),
        ]
        router = LLMRouter(providers=providers)
        result = router.generate("p", task="t", policy=LLMPolicy(provider="c"))
        assert result.provider == "b"
        # Selection is observable: the policy provider is consulted first,
        # then the configured order minus the policy provider.
        skipped = [
            e["provider"] for e in router_events if e["event"] == "llm_provider_skipped"
        ]
        assert skipped == ["c", "a"]
        assert all(p.calls == [] for p in providers[:2])

    def test_policy_fallback_chain_overrides_order(self):
        providers = [FakeProvider(name="a", script=[LLMProviderUnavailable("down")]),
                     FakeProvider(name="z")]
        router = LLMRouter(providers=providers)
        result = router.generate(
            "p", task="t", policy=LLMPolicy(fallback_chain=("a", "z"))
        )
        assert result.provider == "z"

    def test_unknown_provider_in_policy_fails_without_calling_anything(self):
        provider = FakeProvider(name="a")
        router = LLMRouter(providers=[provider])
        with pytest.raises(LLMConfigurationError):
            router.generate("p", task="t", policy=LLMPolicy(provider="ghost"))
        assert provider.calls == []

    def test_task_is_required_and_non_empty(self):
        router = LLMRouter(providers=[FakeProvider()])
        with pytest.raises(TypeError):
            router.generate("p")  # keyword-only, no default
        with pytest.raises(LLMConfigurationError):
            router.generate("p", task="")


# ---------------------------------------------------------------------------
# Fallback (classified, bounded)
# ---------------------------------------------------------------------------


class TestFallback:
    def test_unavailable_provider_falls_back_without_retry(self, router_events):
        dead = FakeProvider(name="dead", available=False)
        live = FakeProvider(name="live", script=["from-live"])
        router = LLMRouter(providers=[dead, live])
        result = router.generate("p", task="t")
        assert result.provider == "live"
        assert dead.calls == []  # not_available is a selection skip
        names = [e["event"] for e in router_events]
        assert names[0] == "llm_router_started"
        assert "llm_provider_skipped" in names
        assert "llm_router_fallback" in names
        assert names[-1] == "llm_router_completed"

    def test_provider_unavailable_from_generate_falls_back(self, router_events):
        first = FakeProvider(name="first", script=[LLMProviderUnavailable("auth rejected")])
        second = FakeProvider(name="second")
        router = LLMRouter(providers=[first, second])
        result = router.generate("p", task="t")
        assert result.provider == "second"
        assert len(first.calls) == 1  # dead provider is not retried

    @pytest.mark.parametrize(
        "error",
        [
            LLMProviderTimeout("slow"),
            LLMProviderRateLimited("429"),
            LLMProviderError("generic provider failure"),
        ],
        ids=["timeout", "rate-limited", "generic"],
    )
    def test_classified_failure_falls_back_to_next_provider(self, error):
        first = FakeProvider(name="first", script=[error])
        second = FakeProvider(name="second", script=["recovered"])
        router = LLMRouter(providers=[first, second])
        result = router.generate("p", task="t")
        assert result.provider == "second"
        assert result.metadata["fallback_used"] is True
        assert result.metadata["fallback_from"] == "first"

    def test_generic_provider_exception_is_normalized_and_falls_back(self):
        # A raw non-typed exception raised by a provider must never escape
        # the router; it is normalized to LLMProviderError.
        first = FakeProvider(name="first", script=[ValueError("raw SDK-ish error")])
        second = FakeProvider(name="second")
        router = LLMRouter(providers=[first, second])
        result = router.generate("p", task="t")
        assert result.provider == "second"

    def test_bounded_retry_then_fallback(self):
        error = LLMProviderError("flaky")
        flaky = FakeProvider(name="flaky", script=[error])
        backup = FakeProvider(name="backup", script=["ok"])
        router = LLMRouter(providers=[flaky, backup], max_attempts_per_provider=3)
        result = router.generate("p", task="t")
        assert len(flaky.calls) == 3
        assert result.provider == "backup"

    def test_retry_recovers_within_one_provider(self):
        error = LLMProviderError("transient")
        flaky = FakeProvider(name="flaky", script=[error, "recovered"])
        router = LLMRouter(providers=[flaky], max_attempts_per_provider=2)
        result = router.generate("p", task="t")
        assert result.text == "recovered"
        assert result.metadata["attempt"] == 2

    def test_configuration_error_is_never_rerouted(self):
        # A configuration failure cannot succeed on another provider:
        # the router must abort, not burn the chain (approved 3.0A policy).
        bad = FakeProvider(name="bad", script=[LLMConfigurationError("bad request config")])
        other = FakeProvider(name="other")
        router = LLMRouter(providers=[bad, other])
        with pytest.raises(LLMConfigurationError):
            router.generate("p", task="t")
        assert other.calls == []

    def test_exhausted_chain_raises_last_provider_neutral_error(self):
        err_a = LLMProviderUnavailable("a down")
        err_b = LLMProviderTimeout("b timed out")
        a = FakeProvider(name="a", script=[err_a])
        b = FakeProvider(name="b", script=[err_b])
        router = LLMRouter(providers=[a, b])
        with pytest.raises(LLMProviderError) as excinfo:
            router.generate("p", task="t")
        assert excinfo.value is err_b
        assert isinstance(excinfo.value, LLMProviderError)

    def test_fallback_events_are_emitted(self, router_events):
        first = FakeProvider(name="first", script=[LLMProviderTimeout("t")])
        second = FakeProvider(name="second")
        router = LLMRouter(providers=[first, second])
        router.generate("p", task="t")
        fallback = [e for e in router_events if e["event"] == "llm_router_fallback"]
        assert fallback and fallback[0]["provider"] == "first"
        assert fallback[0]["next_provider"] == "second"
        assert fallback[0]["error_category"] == "timeout"


# ---------------------------------------------------------------------------
# Budget & result
# ---------------------------------------------------------------------------


class TestBudgetAndResult:
    def test_budget_timeout_is_passed_to_provider(self):
        provider = FakeProvider(name="p")
        router = LLMRouter(providers=[provider])
        router.generate("p", task="t", budget=LLMBudget(timeout_ms=1500))
        assert provider.calls[0]["timeout_seconds"] == pytest.approx(1.5)

    def test_no_budget_means_no_forced_timeout(self):
        provider = FakeProvider(name="p")
        router = LLMRouter(providers=[provider])
        router.generate("p", task="t")
        assert provider.calls[0]["timeout_seconds"] is None

    def test_policy_overrides_reach_the_provider(self):
        provider = FakeProvider(name="p")
        router = LLMRouter(providers=[provider])
        router.generate(
            "p", task="t", policy=LLMPolicy(temperature=0.0, max_tokens=42)
        )
        call = provider.calls[0]
        assert call["temperature"] == 0.0
        assert call["max_tokens"] == 42

    def test_result_is_provider_neutral_and_complete(self):
        provider = FakeProvider(name="prov", model="m-1", script=["text!"])
        router = LLMRouter(providers=[provider])
        result = router.generate("hello", task="sql_generation")
        assert result.text == "text!"
        assert result.provider == "prov"
        assert result.model == "m-1"
        assert result.usage.total_tokens == 0  # Usage available, zero until wired
        assert result.latency_ms >= 0.0
        assert result.metadata["task"] == "sql_generation"
        assert result.metadata["attempt"] == 1
        assert result.metadata["fallback_used"] is False


# ---------------------------------------------------------------------------
# Telemetry hygiene
# ---------------------------------------------------------------------------


class TestTelemetryHygiene:
    def test_events_never_contain_prompt_content(self, router_events):
        secret_prompt = "SECRET-QUESTION-CONTENT must not be logged"
        first = FakeProvider(name="first", script=[LLMProviderError("x")])
        second = FakeProvider(name="second")
        router = LLMRouter(providers=[first, second])
        router.generate(secret_prompt, task="t")
        dumped = repr(router_events)
        assert secret_prompt not in dumped
        assert "SECRET-QUESTION-CONTENT" not in dumped

    def test_completed_event_carries_provider_and_latency(self, router_events):
        provider = FakeProvider(name="prov", script=["ok"])
        router = LLMRouter(providers=[provider])
        router.generate("p", task="answer")
        completed = [e for e in router_events if e["event"] == "llm_router_completed"]
        assert completed and completed[0]["provider"] == "prov"
        assert completed[0]["task"] == "answer"
        assert completed[0]["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Availability & default chain
# ---------------------------------------------------------------------------


class TestDefaultChain:
    @pytest.fixture
    def default_config(self, monkeypatch):
        """Pin routing config deterministically (no env/.env dependence)."""
        from src.utils.config_loader import LLMRoutingConfig

        monkeypatch.setattr(
            "src.core.llm.router.load_llm_routing_config",
            lambda: LLMRoutingConfig(
                provider_order=("openai", "groq", "ollama"),
                configured_providers=frozenset({"openai", "groq", "ollama"}),
            ),
        )
        return ("openai", "groq", "ollama")

    def test_default_order_is_approved_chain(self, default_config):
        router = build_default_router()
        assert router.provider_order == ("openai", "groq", "ollama")

    def test_gemini_is_not_in_the_default_chain(self, default_config):
        router = build_default_router()
        assert "gemini" not in router.provider_order

    def test_gemini_is_registered_and_selectable(self, default_config):
        router = build_default_router()
        # Registered: a policy naming gemini passes order validation.
        # (Live provider construction is NOT attempted here — no network.)
        assert router._resolve_chain(LLMPolicy(provider="gemini"))[0] == "gemini"

    def test_unconfigured_providers_are_skipped(self, monkeypatch):
        from src.utils.config_loader import LLMRoutingConfig

        monkeypatch.setattr(
            "src.core.llm.router.load_llm_routing_config",
            lambda: LLMRoutingConfig(
                provider_order=("openai", "groq", "ollama"),
                configured_providers=frozenset(),  # nothing configured
            ),
        )
        router = LLMRouter()
        with pytest.raises(LLMProviderUnavailable):
            router.generate("p", task="t")

    def test_has_available_provider_is_cheap_and_correct(self, default_config):
        router = build_default_router()
        # With default factories and (pinned) configured providers this is a
        # construction probe — it must not raise regardless of the machine.
        assert isinstance(router.has_available_provider(), bool)

    def test_has_available_provider_false_when_all_unavailable(self):
        router = LLMRouter(providers=[FakeProvider(name="a", available=False)])
        assert router.has_available_provider() is False

    def test_has_available_provider_true_when_one_available(self):
        router = LLMRouter(
            providers=[FakeProvider(name="a", available=False), FakeProvider(name="b")]
        )
        assert router.has_available_provider() is True


# ---------------------------------------------------------------------------
# Trace context propagation (Sprint 3 / Prompt 3.0C.18)
# ---------------------------------------------------------------------------


class TestTraceContextPropagation:
    def test_router_events_carry_trace_context(self, caplog):
        """The router emits through the shared logging surface; the
        JSON formatter attaches the active TraceContext — no second
        telemetry path, and trace propagation is preserved."""
        import json as _json

        from src.utils.logger import (
            JsonFormatter,
            reset_trace_context,
            set_trace_context,
        )

        provider = FakeProvider(name="prov")
        router = LLMRouter(providers=[provider])
        token = set_trace_context(session_id="trace-check")
        try:
            with caplog.at_level(logging.INFO, logger="app"):
                router.generate("p", task="t")
            started = [
                r
                for r in caplog.records
                if getattr(r, "event", None) == "llm_router_started"
            ]
            assert started, "llm_router_started was not emitted"
            payload = _json.loads(JsonFormatter().format(started[0]))
        finally:
            reset_trace_context(token)
        assert payload.get("trace_id"), (
            "router events must propagate the active TraceContext"
        )
