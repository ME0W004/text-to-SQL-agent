# Sprint 1 — Prompt 2 Report: Deterministic SQL Security Core

## Files created

- `src/core/security/__init__.py`
- `src/core/security/sql_security.py`
- `tests/test_security/test_sql_security.py`
- `tests/test_security/test_database_boundary.py`
- `docs/security/sql-security-policy.md`
- `docs/security/sql-validation.md`
- `docs/security/security-test-matrix.md`

## Files modified

- `src/core/sql_extractor/validator.py`: compatibility API delegates to the AST security core.
- `src/core/database/connection.py`: `execute_query` validates before cursor execution; non-query path is fail-closed.

No Prompt, SQL generation, memory, schema, planner, result or UI code was changed.

## Security architecture implemented

SQLGlot TSQL parsing, exact single-substantive-statement enforcement, AST root/node policy, read-only allow-list, DML/DDL/EXEC/transaction/session/external-access rejection, structured decisions and typed reason codes. Input SQL is never rewritten.

## Tests

`uv run pytest -q` passes all existing Evaluation tests plus the new security matrix. The test suite includes valid project-shaped SELECTs, adversarial multi-statement variants, DML/DDL/EXEC, transaction controls, malformed SQL and execution-boundary checks.

## Definition of Done

- [x] SQLGlot AST parsing and TSQL dialect
- [x] Deterministic single-statement/read-only policy
- [x] DML, DDL, EXEC, transaction/session and external-access rejection
- [x] Structured decisions and typed reason codes
- [x] No silent SQL rewrite
- [x] Extractor compatibility and database execution boundary
- [x] Valid, malformed and adversarial tests
- [x] Full regression suite green
- [x] Security documentation updated

## Known limitations

This core does not enforce DB principal permissions, table/column allow-lists, query cost/row limits or semantic correctness. SQLGlot may classify some malformed or unsupported TSQL forms as `SQL_PARSE_ERROR` or a generic non-read-only root rather than a more specific code; they are still rejected. The legacy regex blocklist is no longer the decision maker but remains behaviorally superseded by the AST gate.

## Integration status

The Agent path is covered through `SQLValidator` compatibility and `DatabaseConnection.execute_query`; no live SQL Server/LLM smoke run was performed because external services were unavailable. Prompt 3 was not started.
