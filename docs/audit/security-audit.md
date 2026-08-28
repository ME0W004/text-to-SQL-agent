# Security Audit

Sprint 1 Prompt 2 added the deterministic AST security core documented under `docs/security/`.

Implemented: SQLGlot TSQL parsing; exact single-substantive-statement enforcement; read-only root allow-list; AST rejection of DML/DDL/EXEC/transaction/session control; SELECT INTO and external rowset rejection; structured reason codes; compatibility delegation from `SQLValidator`; validation before the database cursor; and fail-closed `execute_non_query`.

Remaining outside Prompt 2: database principal/session permissions, table/column allow-lists, broad function policy, complexity/result-size controls, query timeout budgeting and execution-error repair. Database permissions are UNKNOWN and require DBA verification. The previously documented TRUNCATE/multi-statement bypass is now blocked by the AST policy and regression tests.
