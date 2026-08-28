# Security Test Matrix

The executable matrix is in `tests/test_security/test_sql_security.py` and `test_database_boundary.py`.

| Family | Examples | Expected |
|---|---|---|
| Valid read-only | SELECT, TOP, CTE, JOIN, GROUP BY, SQL Server OPTION | Accept |
| Multi-statement | SELECT+SELECT, SELECT+DML/DDL/EXEC, comments, multiple semicolons, leading semicolon | Reject `MULTIPLE_STATEMENTS` or fail-closed parse error where TSQL grammar rejects the input |
| DML | INSERT, UPDATE, DELETE, MERGE | Reject `FORBIDDEN_DML` |
| DDL | CREATE, ALTER, DROP, TRUNCATE, SELECT INTO | Reject `FORBIDDEN_DDL` |
| Execution | EXEC procedure, EXECUTE variants | Reject `FORBIDDEN_EXECUTION` or parser error if grammar rejects the variant |
| Transaction/session | BEGIN, COMMIT, ROLLBACK, SET | Reject `FORBIDDEN_TRANSACTION_CONTROL` |
| Context-changing/remote | USE, KILL, OPENQUERY, OPENDATASOURCE | Reject non-read-only or `UNSUPPORTED_SQL_CONSTRUCT` |
| Malformed | empty, whitespace, incomplete SELECT/CTE, garbage, null | Reject `SQL_PARSE_ERROR` or explicit non-read-only root for parsed garbage |
| Integration boundary | invalid query sent to `execute_query`; any `execute_non_query` | Reject before cursor / always reject |

False-positive review covers real project query shapes (TOP, CTE, joins, aggregates and `OPTION (MAXDOP)`). No known bypass remains within the defined single-statement/read-only/execution-construct policy. Database principal permissions, resource cost and table/column authorization are intentionally outside this Prompt.
