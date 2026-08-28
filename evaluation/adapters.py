"""Adapters from repository datasets and the current production Agent."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .models import CanonicalCase, EvaluationResponse


VALID_DIFFICULTIES = {"easy", "medium", "hard"}


class DatasetAdapter:
    """Normalize supported JSON dataset shapes into CanonicalCase objects."""

    def __init__(self, source: str | Path):
        self.source = str(source)

    def load(self) -> list[CanonicalCase]:
        path = Path(self.source)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
            records = payload["cases"]
        elif isinstance(payload, list):
            records = payload
        else:
            raise ValueError("Dataset must be a JSON list or an object containing a cases list.")

        cases = [self._normalize(record, index) for index, record in enumerate(records)]
        ids = [case.id for case in cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"Dataset contains duplicate case ids: {duplicates}")
        return cases

    def _normalize(self, record: Any, index: int) -> CanonicalCase:
        if not isinstance(record, dict):
            raise ValueError(f"Case {index} must be an object.")
        required = ("id", "question", "category", "difficulty")
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"Case {index} missing required fields: {', '.join(missing)}")
        difficulty = str(record["difficulty"]).lower()
        if difficulty not in VALID_DIFFICULTIES:
            raise ValueError(f"Case {record['id']} has unsupported difficulty: {difficulty}")

        tables = record.get("expected_tables")
        columns = record.get("expected_columns")
        if tables is not None and not isinstance(tables, list):
            raise ValueError(f"Case {record['id']} expected_tables must be a list or null.")
        if columns is not None and not isinstance(columns, list):
            raise ValueError(f"Case {record['id']} expected_columns must be a list or null.")

        return CanonicalCase(
            id=str(record["id"]),
            question=str(record["question"]),
            category=str(record["category"]),
            difficulty=difficulty,
            expected_intent=record.get("expected_intent"),
            expected_tables=tables,
            expected_columns=columns,
            expected_behavior=record.get("expected_behavior"),
            gold_sql=record.get("gold_sql"),
            notes=record.get("notes"),
            source=self.source,
        )


def load_dataset(source: str | Path) -> list[CanonicalCase]:
    return DatasetAdapter(source).load()


class EvaluationAgentAdapter:
    """Stable adapter around the current ``TextToSQLAgent.ask`` interface.

    ``agent_factory`` is injected so unit tests can use a fake agent and normal
    imports never require database credentials or an LLM provider.
    """

    def __init__(
        self,
        agent: Any = None,
        agent_factory: Optional[Callable[[], Any]] = None,
        agent_version: str = "unknown",
    ):
        self._agent = agent
        self._agent_factory = agent_factory
        self.agent_version = agent_version

    @property
    def agent(self) -> Any:
        if self._agent is None:
            if self._agent_factory is None:
                raise RuntimeError("No agent or agent_factory was supplied.")
            self._agent = self._agent_factory()
        return self._agent

    def ask(self, question: str, session_id: Optional[str] = None) -> EvaluationResponse:
        started = time.perf_counter()
        try:
            result = self.agent.ask(question, session_id=session_id)
            elapsed = (time.perf_counter() - started) * 1000
            try:
                return self.normalize_response(result, question, elapsed)
            except (TypeError, ValueError) as exc:
                return EvaluationResponse(False, question, error=f"Invalid response: {exc}", latency_ms=elapsed)
        except TimeoutError as exc:
            elapsed = (time.perf_counter() - started) * 1000
            return EvaluationResponse(False, question, error=str(exc) or "Timeout", latency_ms=elapsed)
        except Exception as exc:  # Adapter boundary: a case must not abort a run.
            elapsed = (time.perf_counter() - started) * 1000
            return EvaluationResponse(False, question, error=f"{type(exc).__name__}: {exc}", latency_ms=elapsed)

    @staticmethod
    def normalize_response(result: Any, question: str, latency_ms: float) -> EvaluationResponse:
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        if not isinstance(result, dict):
            raise ValueError("Agent response must be a dictionary-like object.")

        rows = result.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        row_count = result.get("row_count")
        if row_count is None and result.get("success"):
            row_count = len(rows)
        return EvaluationResponse(
            success=bool(result.get("success", False)),
            question=str(result.get("question") or question),
            sql=result.get("sql"),
            answer=result.get("answer"),
            rows=rows,
            row_count=row_count,
            error=result.get("error"),
            session_id=result.get("session_id"),
            latency_ms=latency_ms,
            raw=result,
        )

    def close(self) -> None:
        if self._agent is not None and hasattr(self._agent, "close"):
            self._agent.close()
