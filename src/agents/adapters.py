"""Adapters mapping existing components to target contracts.

Sprint 2 Prompt 2B does NOT reimplement the Security Core or the database
boundary. These adapters wrap the existing components and expose them
through the approved Sprint 2 Prompt 2A contracts.

Adapters MUST:
* Be thin — pass-through to the existing implementation.
* Preserve Sprint 1 / Sprint 2 Prompt 1 security behaviour.
* Be the only place that converts between the old and new contracts.

Future Sprints may move the wrapped components into this module or rename
them. In Sprint 2 Prompt 2B they remain the canonical implementations.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import datetime
from time import perf_counter
from typing import Any, Mapping, Optional, Sequence

from src.core.database.connection import (
    DatabaseConnection,
    ExecutionPolicy,
)
from src.core.security.sql_security import (
    SecurityDecision,
    SecurityPolicy,
    SQLSecurityValidator,
    DEFAULT_SECURITY_POLICY,
)

from src.agents.contracts import QueryResult


__all__ = [
    "SecurityValidatorAdapter",
    "QueryExecutorAdapter",
    "default_answer_fn",
]


class SecurityValidatorAdapter:
    """Adapter exposing ``SQLSecurityValidator`` as the ``SecurityValidator`` Protocol."""

    def __init__(self, validator: Optional[SQLSecurityValidator] = None,
                 policy: SecurityPolicy = DEFAULT_SECURITY_POLICY) -> None:
        self._validator = validator or SQLSecurityValidator(policy)
        self._policy = policy

    def validate(self, sql: str, *, policy: Optional[SecurityPolicy] = None) -> SecurityDecision:
        active = policy or self._policy
        return self._validator.validate(sql) if active is self._policy else \
            SQLSecurityValidator(active).validate(sql)


class QueryExecutorAdapter:
    """Adapter exposing ``DatabaseConnection.execute_query`` as the ``QueryExecutor`` Protocol.

    This adapter:

    * Maps the legacy ``list[dict]`` return to a typed ``QueryResult``.
    * Preserves the inline SecurityValidator call made by
      ``DatabaseConnection.execute_query`` (Sprint 1 Prompt 2 contract).
    * Preserves all execution semantics (fresh connection/cursor, row
      caps, timeouts, typed error mapping).
    * Adds per-call ``duration_ms`` timing derived from ``perf_counter``.
    """

    def __init__(self, connection: DatabaseConnection) -> None:
        self._connection = connection

    def execute(
        self,
        sql: str,
        *,
        params: Optional[Sequence[Any] | Mapping[str, Any]] = None,
        policy: ExecutionPolicy = ...,
    ) -> QueryResult:
        started = perf_counter()
        # DatabaseConnection.execute_query already runs the SecurityValidator
        # inline. We rely on that documented integration (see §10 in
        # component-contracts.md) and only time the call here.
        rows = self._connection.execute_query(sql, params)
        duration_ms = (perf_counter() - started) * 1000.0
        columns: tuple[str, ...] = ()
        if rows:
            columns = tuple(rows[0].keys())
        return QueryResult(
            columns=columns,
            rows=tuple(rows),
            row_count=len(rows),
            truncated=False,
            duration_ms=duration_ms,
            executed_at=datetime.utcnow(),
        )


def default_answer_fn(prompts: Any, llm_client: Any):
    """Transitional adapter for the current-path answer generation.

    Builds the callable the Orchestrator uses to turn
    ``(question, rows, row_count)`` into a natural-language answer while
    the future AnswerGenerator/AnswerValidator components are not yet
    wired. This module — not the Orchestrator — owns the knowledge of
    the prompt template, keeping the Orchestrator free of prompt
    construction. It will be removed when AnswerGenerator lands.
    """

    def _answer(question: str, rows: list, row_count: int) -> str:
        prompt = prompts.natural_response_prompt(
            question=question, rows=rows, row_count=row_count
        )
        return llm_client.generate(prompt)

    return _answer