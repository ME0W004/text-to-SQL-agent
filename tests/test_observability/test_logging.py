import asyncio
import json
import logging

from src.utils.logger import (
    JsonFormatter,
    get_trace_context,
    log_event,
    new_trace_id,
    reset_trace_context,
    sanitize,
    set_trace_context,
    sql_fingerprint,
)


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []
        self.setFormatter(JsonFormatter())

    def emit(self, record):
        self.lines.append(self.format(record))


def test_trace_id_generation_and_structured_event_schema():
    trace_id = new_trace_id()
    assert len(trace_id) == 32
    handler = ListHandler()
    logger = logging.getLogger("test.structured")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    token = set_trace_context(trace_id, "session-1")
    try:
        log_event("request_started", logger=logger, component="api", duration_ms=1.5)
    finally:
        reset_trace_context(token)

    payload = json.loads(handler.lines[0])
    assert payload["event"] == "request_started"
    assert payload["component"] == "api"
    assert payload["trace_id"] == trace_id
    assert payload["session_id"] == "session-1"
    assert payload["duration_ms"] == 1.5
    assert payload["timestamp"].endswith("+00:00")


def test_sensitive_fields_and_payloads_are_centrally_redacted():
    raw_sql = "SELECT salary FROM payroll WHERE employee='Alice'"
    secret = "super-secret-value"
    sanitized = sanitize({
        "password": secret,
        "api_key": secret,
        "authorization": secret,
        "connection_string": secret,
        "sql": raw_sql,
        "question": "private customer question",
        "rows": [{"salary": 999}],
        "safe": "kept",
    })
    encoded = json.dumps(sanitized)
    assert secret not in encoded
    assert raw_sql not in encoded
    assert "private customer question" not in encoded
    assert "999" not in encoded
    assert sanitized["safe"] == "kept"


def test_message_secret_redaction_and_json_newline_safety():
    handler = ListHandler()
    logger = logging.getLogger("test.injection")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    log_event(
        "test_event", logger=logger,
        message="authorization=Bearer-secret\n{\"event\":\"forged\"}",
        user_label="line1\nline2\tvalue",
    )
    assert len(handler.lines) == 1
    payload = json.loads(handler.lines[0])
    assert "Bearer-secret" not in handler.lines[0]
    assert payload["event"] == "test_event"
    assert payload["user_label"] == "line1\nline2\tvalue"


def test_sql_fingerprint_is_stable_and_does_not_contain_sql():
    sql = " SELECT  *\nFROM Sale.Orders "
    fingerprint = sql_fingerprint(sql)
    assert fingerprint == sql_fingerprint("select * from sale.orders")
    assert len(fingerprint) == 64
    assert "orders" not in fingerprint


def test_contextvars_isolate_concurrent_traces():
    async def worker(trace_id):
        token = set_trace_context(trace_id)
        try:
            await asyncio.sleep(0)
            return get_trace_context().trace_id
        finally:
            reset_trace_context(token)

    async def run_workers():
        return await asyncio.gather(worker("trace-a"), worker("trace-b"))

    assert asyncio.run(run_workers()) == ["trace-a", "trace-b"]
