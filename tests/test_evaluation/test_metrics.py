from evaluation.metrics import aggregate_metrics, case_metrics, classify_failure
from evaluation.models import EvaluationResponse


def response(**kwargs):
    defaults = {"success": False, "question": "q", "latency_ms": 10.0}
    defaults.update(kwargs)
    return EvaluationResponse(**defaults)


def test_failure_classification():
    assert classify_failure(response(success=True, row_count=1)) == "SUCCESS"
    assert classify_failure(response(success=True, row_count=0)) == "EMPTY_RESULT"
    assert classify_failure(response(error="Invalid SQL: forbidden keyword")) == "VALIDATION_FAILURE"
    assert classify_failure(response(error="Database error: connection failed")) == "DATABASE_ERROR"
    assert classify_failure(response(error="OpenAI generation failed")) == "LLM_ERROR"
    assert classify_failure(response(error="Request timed out")) == "TIMEOUT"
    assert classify_failure(response(error="Invalid response: not a mapping")) == "INVALID_RESPONSE"
    assert classify_failure(response(error="something else")) == "UNKNOWN_ERROR"


def test_metrics_and_aggregation():
    ok = response(success=True, sql="SELECT 1", row_count=1, latency_ms=10)
    empty = response(success=True, sql="SELECT 1 WHERE 1=0", row_count=0, latency_ms=20)
    bad = response(error="Invalid SQL", latency_ms=30)
    records = []
    for item in (ok, empty, bad):
        category = classify_failure(item)
        records.append({
            "metrics": case_metrics(item, category),
            "failure_category": None if category in {"SUCCESS", "EMPTY_RESULT"} else category,
            "latency_ms": item.latency_ms,
        })
    aggregate = aggregate_metrics(records)
    assert aggregate["rates"]["response_success"] == 0.6667
    assert aggregate["rates"]["execution_success"] == 0.6667
    assert aggregate["latency_ms"]["mean_ms"] == 20
    assert aggregate["failure_categories"] == {"VALIDATION_FAILURE": 1}
