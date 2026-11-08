# tests/test_llm/test_import_purity.py

"""Import purity (Sprint 3B STEP 12 / STEP 13.11).

Fresh-subprocess probes proving that importing the LLM abstraction
surfaces — and the SQL extraction path — loads no provider SDK and no
HTTP client. This is the executable form of the boundary: the provider
SDK loads only when a configured provider is actually constructed.
"""

from __future__ import annotations

import functools
import json
import pathlib
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

SDK_MARKERS = ("openai", "langchain", "google.genai", "requests", "groq", "ollama")

# Network surfaces used by the (removed) connectivity ping and by provider
# SDKs; patched to raise for import-time side-effect probes.
_HTTP_PATCH = """
for _target, _attr in (
    ("requests", "get"), ("requests", "request"),
    ("httpx", "request"), ("urllib.request", "urlopen"),
):
    try:
        _mod = __import__(_target, fromlist=["__trash__"])
    except ImportError:
        continue
    setattr(_mod, _attr, _forbid)
"""

_PROBE = """
import json, sys
import {module}
{post}
loaded = sorted(m for m in sys.modules
                if any(m == p or m.startswith(p + ".") for p in {markers!r}))
llm_modules = sorted(m for m in sys.modules if m.startswith("src.core.llm"))
src_modules = sorted(m for m in sys.modules if m.startswith("src."))
print(json.dumps({{"loaded": loaded, "llm_modules": llm_modules,
                   "src_modules": src_modules,
                   "pymssql": "pymssql" in sys.modules}}))
"""


@functools.lru_cache(maxsize=None)
def _probe(module: str, post: str = "") -> dict:
    """Fresh-subprocess probe, cached per (module, post): each protected
    surface is spawned at most once per test run (3.0C.34 — probes for
    different properties stay separate tests but share the result)."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(module=module, post=post, markers=SDK_MARKERS)],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"probe for {module!r} failed "
            f"(rc={result.returncode}):\n{result.stderr}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "module",
    [
        "src.core.llm.policy",
        "src.core.llm.errors",
        "src.core.llm.base",
        "src.core.llm.router",
        "src.core.llm.client",
        "src.core.sql_extractor.extractor",
        "src.agents.contracts",
        "src.agents.state",
    ],
)
def test_importing_module_loads_no_provider_sdk(module):
    probe = _probe(module)
    assert probe["loaded"] == [], (
        f"importing {module} loaded provider/HTTP modules: {probe['loaded']}"
    )


def test_extractor_import_path_contains_no_llm_module():
    # STEP 8: SQLExtractor must not import any src.core.llm module.
    probe = _probe("src.core.sql_extractor.extractor")
    assert probe["llm_modules"] == [], (
        f"SQLExtractor import path pulled in LLM modules: {probe['llm_modules']}"
    )


def test_building_default_router_loads_no_sdk():
    # Lazy factories: constructing a router (even with the default
    # registry) must not import any provider SDK or provider client
    # module.
    probe = _probe(
        "src.core.llm.router",
        post="router = src.core.llm.router.build_default_router()",
    )
    assert probe["loaded"] == []
    assert "src.core.llm.openai_client" not in probe["llm_modules"]
    assert "src.core.llm.groq_client" not in probe["llm_modules"]
    assert "src.core.llm.gemini_client" not in probe["llm_modules"]
    assert "src.core.llm.ollama_client" not in probe["llm_modules"]


def test_facade_import_no_longer_loads_provider_sdk():
    # F3 debt narrowed: importing the facade used to transitively load the
    # openai SDK (facade → LLMClient → OpenAIClient). Since Sprint 3B the
    # SDK loads only when a provider is actually constructed. (pymssql is
    # still loaded here — pre-existing, documented facade construction debt.)
    probe = _probe("src.agents.text_to_sql_agent")
    assert probe["loaded"] == [], (
        f"facade import loaded provider/HTTP modules: {probe['loaded']}"
    )


# ---------------------------------------------------------------------------
# Orchestrator footprint (Sprint 3 / Prompt 3.0C.29)
# ---------------------------------------------------------------------------


_PROVIDER_CLIENT_MODULES = (
    "src.core.llm.openai_client",
    "src.core.llm.groq_client",
    "src.core.llm.gemini_client",
    "src.core.llm.ollama_client",
)


def test_orchestrator_import_footprint():
    """The Orchestrator is an application module: it depends on
    abstractions and approved composition components, not provider SDKs.

    Documented remaining footprint (pre-existing, NOT part of the LLM
    boundary): the DB driver module loads via the approved
    ``src.core.database.connection`` composition import. The openai F3
    chain is gone and this probe pins its removal.
    """
    probe = _probe("src.agents.orchestrator")
    assert probe["loaded"] == [], (
        f"importing the Orchestrator loaded provider/HTTP modules: {probe['loaded']}"
    )
    for provider_module in _PROVIDER_CLIENT_MODULES:
        assert provider_module not in probe["llm_modules"], (
            f"Orchestrator import loaded provider implementation {provider_module}"
        )
    assert "src.memory.manager" not in probe["src_modules"], (
        "Orchestrator must receive memory via injection, not import it"
    )
    assert "src.agents.text_to_sql_agent" not in probe["src_modules"], (
        "Orchestrator must not import its own façade"
    )
    assert probe["pymssql"] is True, (
        "expected the documented DB-driver load via "
        "src.core.database.connection (composition import, pre-existing debt)"
    )


# ---------------------------------------------------------------------------
# Provider import-time side effects (Sprint 3 / Prompt 3.0C.8)
# ---------------------------------------------------------------------------

_PROVIDER_SIDE_EFFECT_PROBE = """
import json, sys

def _forbid(*args, **kwargs):
    raise AssertionError("forbidden import-time action")

# If a provider module constructed a live client at import time, this
# pre-patched constructor raising makes the import fail.
for _sdk, _ctor in (("openai", "OpenAI"), ("langchain_groq", "ChatGroq"),
                    ("google.genai", "Client"), ("langchain_ollama", "OllamaLLM")):
    try:
        _mod = __import__(_sdk, fromlist=["__trash__"])
    except ImportError:
        continue
    if hasattr(_mod, _ctor):
        setattr(_mod, _ctor, _forbid)

# Any network call at import time fails the probe.
for _target, _attr in (("requests", "get"), ("requests", "request"),
                       ("requests", "post"), ("httpx", "request"),
                       ("httpx", "get"), ("httpx", "post"),
                       ("urllib.request", "urlopen")):
    try:
        _mod = __import__(_target, fromlist=["__trash__"])
    except ImportError:
        continue
    setattr(_mod, _attr, _forbid)

import {module}
print(json.dumps({{"ok": True}}))
"""


@functools.lru_cache(maxsize=None)
def _provider_import_probe(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROVIDER_SIDE_EFFECT_PROBE.format(
            module=module,
            markers=SDK_MARKERS,
        )],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"importing {module!r} had import-time side effects "
            f"(rc={result.returncode}):\n{result.stderr}"
        )


@pytest.mark.parametrize(
    "module",
    [
        "src.core.llm.openai_client",
        "src.core.llm.groq_client",
        "src.core.llm.gemini_client",
        "src.core.llm.ollama_client",
    ],
)
def test_provider_module_import_has_no_side_effects(module):
    """Importing a provider module must not construct a live provider
    client, make a network call, or send credentials. Harmless static
    initialization (class definitions, ``load_dotenv()`` env reads) is
    explicitly not a failure."""
    _provider_import_probe(module)
