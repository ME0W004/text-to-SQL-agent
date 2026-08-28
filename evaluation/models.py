"""Stable data contracts used by the evaluation harness.

These models intentionally do not require the production Agent or any external
service, which keeps parsing, reporting and comparison deterministic offline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class CanonicalCase:
    id: str
    question: str
    category: str
    difficulty: str
    expected_intent: Any = None
    expected_tables: Optional[list[str]] = None
    expected_columns: Optional[list[str]] = None
    expected_behavior: Any = None
    gold_sql: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationResponse:
    success: bool
    question: str
    sql: Optional[str] = None
    answer: Optional[str] = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: Optional[int] = None
    error: Optional[str] = None
    session_id: Optional[str] = None
    latency_ms: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass
class CaseResult:
    case_id: str
    question: str
    status: str
    failure_category: Optional[str]
    metrics: dict[str, Any]
    actual: dict[str, Any]
    expected: dict[str, Any]
    failure: Optional[str]
    latency_ms: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationRun:
    run_id: str
    timestamp: str
    dataset: str
    agent_version: str
    total_cases: int
    passed: int
    failed: int
    metrics: dict[str, Any]
    cases: list[CaseResult]
    mode: str = "online"
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cases"] = [case.to_dict() for case in self.cases]
        return data
