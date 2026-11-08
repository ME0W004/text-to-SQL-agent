"""Provider substitution proof (Sprint 3 / Prompt 3.0C.9).

The application architecture must *depend on the abstraction*, not
merely have it present. This test executes the complete pipeline

    FakeProvider -> LLMRouter -> SQLExtractor / answer seam -> Orchestrator

in a fresh subprocess and asserts that none of the real provider SDKs
(``openai``, ``langchain``, ``google.genai``, ``groq``, ``ollama``) or
``requests`` is ever imported. This is an architecture test, not an LLM
quality test: it proves the Orchestrator's LLM-dependent path runs
entirely against the injected contract.

The subprocess is essential — the pytest process itself imports provider
SDKs for exception-mapping unit tests, which would contaminate
``sys.modules``.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Everything the substitution path must NOT load.
SDK_MARKERS = ("openai", "langchain", "google.genai", "groq", "ollama", "requests")

_SCRIPT = '''
import json, sys

# --- fake provider behind the canonical internal interface -------------
from src.core.llm.base import BaseLLMClient


class FakeProvider(BaseLLMClient):
    """Serves the SQL-generation call, then the answer call."""

    name = "fake"
    model = "fake-model"

    def __init__(self):
        self.script = ["SELECT 1 AS v", "The answer is 1."]
        self.calls = []

    def is_available(self):
        return True

    def generate(self, prompt, *, timeout_seconds=None, temperature=None,
                 max_tokens=None):
        self.calls.append(prompt)
        return self.script.pop(0)


# --- routing + task-bound adapters (the production composition) --------
from src.core.llm.router import LLMRouter
from src.core.llm.client import LLMClient
from src.core.sql_extractor.extractor import SQLExtractor

router = LLMRouter(providers=[FakeProvider()])
sql_client = LLMClient(router, task="sql_generation")
answer_client = LLMClient(router, task="response_generation")

# --- in-memory database boundary (mirrors the compat-suite fakes) ------
from src.core.database.connection import DatabaseConnection, ExecutionPolicy
from src.agents.adapters import QueryExecutorAdapter


class FakeCursor:
    arraysize = 1

    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchmany(self, size):
        result, self.rows = self.rows[:size], self.rows[size:]
        return result

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor([{"v": 1}])

    def cursor(self, as_dict=True):
        return self.cursor_instance

    def close(self):
        pass


db = DatabaseConnection(
    "host", 1433, "user", "password", "database",
    execution_policy=ExecutionPolicy(max_rows=10, max_fetch_batch=2,
                                     query_timeout=5, connection_timeout=2),
    connect_fn=lambda **kwargs: FakeConnection(),
)

# --- in-memory memory boundary ------------------------------------------
class FakeMemory:
    def __init__(self):
        self.messages = []

    def create_session(self):
        return "s1"

    def session_exists(self, session_id):
        return bool(session_id)

    def get_history(self, session_id, limit=5):
        return []

    def add_message(self, session_id, role, content):
        self.messages.append((role, content))

    def clear_session(self, session_id):
        pass


# --- the real Orchestrator over injected abstractions -------------------
from src.core.prompts import SystemPrompts
from src.agents.orchestrator import Orchestrator, OrchestratorDeps
from src.agents.state import create_initial_state

deps = OrchestratorDeps(
    sql_extractor=SQLExtractor(sql_client),
    query_executor=QueryExecutorAdapter(db),
    llm_client=answer_client,
    prompts=SystemPrompts(),
    memory_manager=FakeMemory(),
    execution_policy=db.execution_policy,
    llm_router=router,
)
state = Orchestrator(deps).run(create_initial_state(question="how many?"))

sdk_modules = sorted(
    m for m in sys.modules
    if any(m == p or m.startswith(p + ".") for p in __markers__)
)
print(json.dumps({
    "success": state.is_success(),
    "sql": state.sql.generated_sql or "",
    "provider_calls": len(router._instances["fake"].calls),
    "sdk_modules": sdk_modules,
}))
'''.replace("__markers__", repr(SDK_MARKERS))


def test_orchestrator_runs_on_fake_provider_without_any_provider_sdk():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"substitution pipeline failed (rc={result.returncode}):\n{result.stderr}"
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["success"] is True, (
        f"pipeline did not succeed: {payload}"
    )
    assert "SELECT" in payload["sql"].upper()
    assert payload["provider_calls"] == 2, (
        "expected one SQL-generation and one answer call through the router"
    )
    assert payload["sdk_modules"] == [], (
        "the application path imported real provider SDKs: "
        f"{payload['sdk_modules']}"
    )
