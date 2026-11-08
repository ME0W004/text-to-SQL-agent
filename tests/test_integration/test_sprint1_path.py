import logging
import pytest
from fastapi.testclient import TestClient

from evaluation.adapters import EvaluationAgentAdapter
from src.agents.text_to_sql_agent import TextToSQLAgent
from src.api.main import app
from src.api import routes
from src.core.database.connection import DatabaseConnection, ExecutionPolicy
from src.core.security.sql_security import SQLSecurityError
from src.utils.config_loader import ConfigurationError, load_config
from src.utils.logger import get_trace_context, reset_trace_context, set_trace_context


class FakeCursor:
    arraysize = 1

    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchmany(self, size):
        result, self.rows = self.rows[:size], self.rows[size:]
        return result

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.closed = False

    def cursor(self, as_dict=True):
        return self.cursor_instance

    def close(self):
        self.closed = True


class FakeExtractor:
    def extract(self, question, history=None):
        return "SELECT 1 AS value"


class FakeLLM:
    def generate(self, prompt):
        return "The value is 1."


class FakeMemory:
    def __init__(self):
        self.session = "session-integration"

    def create_session(self):
        return self.session

    def session_exists(self, session_id):
        return bool(session_id)

    def get_history(self, session_id, limit=5):
        return []

    def add_message(self, session_id, role, content):
        pass


class FakePrompts:
    def natural_response_prompt(self, question, rows, row_count):
        return "summarize"


def test_agent_real_path_uses_security_execution_and_trace(monkeypatch, caplog):
    cursor = FakeCursor([{"value": 1}])
    db = DatabaseConnection(
        "host", 1433, "user", "password", "database",
        execution_policy=ExecutionPolicy(max_rows=10, max_fetch_batch=2, query_timeout=5, connection_timeout=2),
        connect_fn=lambda **kwargs: FakeConnection(cursor),
    )
    agent = object.__new__(TextToSQLAgent)
    agent.db = db
    agent.extractor = FakeExtractor()
    agent.llm = FakeLLM()
    agent.memory = FakeMemory()
    agent.prompts = FakePrompts()
    caplog.set_level(logging.INFO)
    token = set_trace_context("integration-trace")
    try:
        result = agent.ask("what is the value?")
    finally:
        reset_trace_context(token)

    assert result["success"] is True
    assert result["rows"] == [{"value": 1}]
    assert cursor.executed == ["SELECT 1 AS value"]
    assert cursor.closed
    events = [getattr(record, "event", None) for record in caplog.records]
    assert events.index("sql_validation_passed") < events.index("db_execution_started")
    assert "db_execution_completed" in events
    assert "agent_completed" in events


def test_agent_security_failure_is_returned_without_database_call(monkeypatch):
    called = []
    db = object.__new__(DatabaseConnection)
    db.security_validator = type("Validator", (), {"validate": lambda self, sql: (_ for _ in ()).throw(RuntimeError("validator down"))})()
    db.execution_policy = ExecutionPolicy()
    db._connect_fn = lambda **kwargs: called.append(kwargs)
    db.host = db.port = db.user = db.password = db.database = "x"
    agent = object.__new__(TextToSQLAgent)
    agent.db = db
    agent.extractor = type("Extractor", (), {"extract": lambda self, question, history=None: "SELECT 1"})()
    agent.memory = FakeMemory()
    agent.llm = FakeLLM()
    agent.prompts = FakePrompts()
    result = agent.ask("question")
    assert result["success"] is False
    assert "Invalid SQL" in result["error"]
    assert called == []


def test_evaluation_adapter_uses_public_agent_path_with_trace():
    seen = []

    class Agent:
        def ask(self, question, session_id=None):
            token = set_trace_context()
            try:
                seen.append(get_trace_context().trace_id)
                return {"success": True, "question": question, "sql": "SELECT 1", "rows": [], "row_count": 0}
            finally:
                reset_trace_context(token)

    response = EvaluationAgentAdapter(agent=Agent()).ask("question")
    assert response.success is True
    assert response.sql == "SELECT 1"
    assert len(seen) == 1


def test_api_health_and_error_contracts_remain_compatible(monkeypatch):
    class HealthAgent:
        db = object()
        llm = type("Llm", (), {"is_available": lambda self: True})()

    monkeypatch.setattr(routes, "get_agent", lambda: HealthAgent())
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        invalid = client.post("/api/v1/ask", json={})
    assert health.status_code == 200
    assert set(health.json()) == {"status", "database", "llm"}
    assert invalid.status_code == 422

    monkeypatch.setattr(routes, "get_agent", lambda: (_ for _ in ()).throw(RuntimeError("private-internal-detail")))
    with TestClient(app) as client:
        failed = client.post("/api/v1/ask", json={"question": "q"})
    assert failed.status_code == 200
    assert failed.json()["error"] == "Internal server error."
    assert "private-internal-detail" not in failed.text


def test_health_availability_is_configuration_based_without_network(monkeypatch):
    """Sprint 3B: /health availability is configuration/provider-based.

    The legacy pre-call connectivity ping was removed (the approved 3.0A
    behavioral exception). This pins the new semantics explicitly: the
    availability check goes through the LLMRouter's configuration-based
    probe and /health performs no network access at all.
    """
    from src.core.llm.client import LLMClient
    from src.core.llm.router import LLMRouter

    class ConfiguredFakeProvider:
        name = "fake"
        model = "fake-model"

        def is_available(self):
            return True

        def generate(self, prompt, **kwargs):
            raise AssertionError("health must never generate")

    class HealthAgent:
        db = object()
        llm = LLMClient(
            LLMRouter(providers=[ConfiguredFakeProvider()]),
            task="response_generation",
        )

    def forbid_http(*args, **kwargs):
        raise AssertionError(
            "HTTP request attempted from /health — the pre-call connectivity "
            "ping was removed by the approved Sprint 3A design"
        )

    # The removed ping used requests.get("https://api.openai.com"); forbid
    # every plausible HTTP surface instead of patching sockets (the ASGI
    # event loop legitimately uses loopback sockets internally).
    monkeypatch.setattr("requests.get", forbid_http, raising=False)
    monkeypatch.setattr("requests.request", forbid_http, raising=False)
    monkeypatch.setattr("httpx.request", forbid_http, raising=False)
    monkeypatch.setattr("httpx.Client.send", forbid_http, raising=False)
    monkeypatch.setattr("urllib.request.urlopen", forbid_http, raising=False)
    monkeypatch.setattr(routes, "get_agent", lambda: HealthAgent())
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert set(health.json()) == {"status", "database", "llm"}
    assert health.json()["llm"] == "Available"


@pytest.mark.parametrize("value", ["invalid", "0", "65536"])
def test_invalid_database_port_configuration_fails_closed(monkeypatch, value):
    for key in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        monkeypatch.setenv(key, "configured")
    monkeypatch.setenv("DB_PORT", value)
    with pytest.raises(ConfigurationError):
        load_config()
