import logging

from fastapi.testclient import TestClient

import run as cli
from src.api.main import app
from src.api import routes
from src.core.database.connection import DatabaseErrorCode
from src.utils.logger import TRACE_ID_HEADER, get_trace_context


class FakeAgent:
    def __init__(self, result=None):
        self.result = result or {
            "success": True, "question": "q", "answer": "a", "sql": "SELECT 1",
            "row_count": 0, "rows": [], "session_id": "session-new",
        }

    def ask(self, question, session_id=None):
        assert get_trace_context() is not None
        return {**self.result, "question": question}


def test_api_trace_header_and_request_lifecycle(monkeypatch, caplog):
    monkeypatch.setattr(routes, "get_agent", lambda: FakeAgent())
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ask", json={"question": "private question"},
            headers={TRACE_ID_HEADER: "client-trace-123"},
        )
    assert response.status_code == 200
    assert response.headers[TRACE_ID_HEADER] == "client-trace-123"
    events = [getattr(record, "event", None) for record in caplog.records]
    assert "request_started" in events
    assert "request_completed" in events


def test_invalid_inbound_trace_id_is_replaced(monkeypatch):
    monkeypatch.setattr(routes, "get_agent", lambda: FakeAgent())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/ask", json={"question": "q"},
            headers={TRACE_ID_HEADER: "bad\ntrace"},
        )
    assert response.headers[TRACE_ID_HEADER] != "bad\ntrace"
    assert len(response.headers[TRACE_ID_HEADER]) == 32


def test_cli_creates_a_trace_for_each_question(monkeypatch):
    seen = []

    class CliAgent:
        def ask(self, question):
            seen.append(get_trace_context().trace_id)
            return {"success": False, "error": "expected", "sql": None}

        def close(self):
            pass

    answers = iter(["first", "second", "exit"])
    monkeypatch.setattr(cli, "TextToSQLAgent", CliAgent)
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    cli.main()
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_database_failure_event_contains_code_not_raw_driver_message(caplog):
    from tests.test_database.test_safe_execution import ConnectionFactory, make_db
    import pymssql

    raw = "connection refused server=secret-host password=do-not-log"
    caplog.set_level(logging.ERROR)
    try:
        make_db(ConnectionFactory(error=pymssql.OperationalError(raw))).execute_query("SELECT 1")
    except Exception:
        pass
    records = [record for record in caplog.records if getattr(record, "event", None) == "db_execution_failed"]
    assert records
    assert records[-1].error_code == DatabaseErrorCode.DATABASE_CONNECTION_ERROR.value
    assert raw not in records[-1].getMessage()


def test_security_rejection_logs_reason_and_fingerprint_without_raw_sql(caplog):
    from tests.test_database.test_safe_execution import ConnectionFactory, make_db

    sql = "SELECT 1; DROP TABLE private_customer_data"
    caplog.set_level(logging.WARNING)
    try:
        make_db(ConnectionFactory([])).execute_query(sql)
    except Exception:
        pass
    records = [record for record in caplog.records if getattr(record, "event", None) == "sql_validation_failed"]
    assert records
    assert records[-1].reason_code == "MULTIPLE_STATEMENTS"
    assert len(records[-1].sql_fingerprint) == 64
    assert sql not in records[-1].getMessage()


def test_sql_extractor_emits_llm_timing_events_without_prompt(caplog):
    from src.core.sql_extractor.extractor import SQLExtractor

    class Llm:
        def generate(self, prompt):
            return "SELECT 1"

    caplog.set_level(logging.INFO)
    assert SQLExtractor(Llm()).extract("private question") == "SELECT 1"
    events = [getattr(record, "event", None) for record in caplog.records]
    assert "llm_call_started" in events
    completed = next(record for record in caplog.records if getattr(record, "event", None) == "llm_call_completed")
    assert completed.purpose == "sql_generation"
    assert completed.duration_ms >= 0
    assert "private question" not in completed.getMessage()
