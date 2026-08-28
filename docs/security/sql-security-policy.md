# SQL Security Policy

The canonical policy is implemented by `src/core/security/sql_security.py` and is independent of LLM prompts and database connectivity.

## Allowed

- Exactly one SQL Server `SELECT` statement.
- `WITH ... SELECT` CTEs.
- Read-only joins, grouping, ordering, aggregates and SQL Server query options that remain inside a `SELECT` AST.

## Rejected

- Multiple statements, including empty fragments around semicolons.
- DML: `INSERT`, `UPDATE`, `DELETE`, `MERGE`.
- DDL: `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, and `SELECT INTO`.
- Procedure/command execution: `EXEC` / `EXECUTE` ASTs.
- Transaction or session control: `BEGIN`, `COMMIT`, `ROLLBACK`, `SET`.
- Non-read-only roots such as `USE` and `KILL`.
- External data access functions `OPENROWSET`, `OPENQUERY`, and `OPENDATASOURCE`.

The policy does not silently strip clauses, rewrite SQL, or retry. It returns the original SQL only as diagnostic data in a typed decision/error. Database credentials must still be read-only; credential permissions are outside repository evidence and remain an operational prerequisite.
