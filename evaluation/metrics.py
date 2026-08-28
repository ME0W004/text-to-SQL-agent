"""Conservative metrics supported without inventing semantic correctness."""

from __future__ import annotations

from collections import Counter
from statistics import mean, median
from typing import Any

from .models import CanonicalCase, EvaluationResponse


PASS_STATUSES = {"SUCCESS", "EMPTY_RESULT"}


def classify_failure(response: EvaluationResponse | None, exception: Exception | None = None) -> str:
    if exception is not None:
        if isinstance(exception, TimeoutError):
            return "TIMEOUT"
        return "EVALUATION_ERROR"
    if response is None:
        return "INVALID_RESPONSE"
    if response.success:
        return "EMPTY_RESULT" if response.row_count == 0 else "SUCCESS"

    message = (response.error or "").lower()
    if "invalid response" in message:
        return "INVALID_RESPONSE"
    if "timeout" in message or "timed out" in message:
        return "TIMEOUT"
    if any(token in message for token in ("database", "pymssql", "sql server", "query execution")):
        return "DATABASE_ERROR"
    if any(token in message for token in ("validation", "invalid sql", "forbidden keyword")):
        return "VALIDATION_FAILURE"
    if any(token in message for token in ("llm", "openai", "model", "provider")):
        return "LLM_ERROR"
    if "sql" in message and any(token in message for token in ("generat", "extract")):
        return "SQL_GENERATION_FAILURE"
    return "UNKNOWN_ERROR"


def case_metrics(response: EvaluationResponse, failure_category: str) -> dict[str, Any]:
    return {
        "response_success": response.success,
        "sql_present": bool(response.sql and response.sql.strip()),
        "execution_success": response.success and bool(response.sql and response.sql.strip()),
        "empty_result": response.success and response.row_count == 0,
        "latency_ms": response.latency_ms,
        "failure_category": None if failure_category in PASS_STATUSES else failure_category,
    }


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {"total_cases": 0, "rates": {}, "latency_ms": {}, "failure_categories": {}}

    def rate(name: str) -> float:
        return round(sum(bool(item["metrics"].get(name)) for item in results) / total, 4)

    latencies = [item["latency_ms"] for item in results if item["latency_ms"] is not None]
    failures = Counter(
        item["failure_category"]
        for item in results
        if item.get("failure_category") and item["failure_category"] not in PASS_STATUSES
    )
    latency = {
        "count": len(latencies),
        "mean_ms": round(mean(latencies), 2) if latencies else None,
        "median_ms": round(median(latencies), 2) if latencies else None,
        "min_ms": round(min(latencies), 2) if latencies else None,
        "max_ms": round(max(latencies), 2) if latencies else None,
    }
    return {
        "total_cases": total,
        "rates": {
            "response_success": rate("response_success"),
            "sql_present": rate("sql_present"),
            "execution_success": rate("execution_success"),
            "empty_result": rate("empty_result"),
        },
        "latency_ms": latency,
        "failure_categories": dict(sorted(failures.items())),
    }
