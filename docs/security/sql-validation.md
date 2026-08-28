# SQL Validation Flow

```text
raw SQL -> sqlglot.parse(dialect="tsql") -> statement list
         -> exact single-statement check
         -> AST node policy checks
         -> read-only root check
         -> accept original SQL or reject with reason code
```

`parse_one` is intentionally not used for statement counting because SQLGlot can return a `Block` for multiple statements. The validator uses `parse`; `None` empty fragments and more than one parsed statement are rejected. A trailing semicolon/comment terminator represented as `Semicolon` is ignored only when there is still exactly one substantive statement.

The compatibility `SQLValidator.validate(sql) -> bool` API remains available to `SQLExtractor`; it delegates to the security core and raises the existing `SQLValidationError` with the security reason code. The authoritative execution boundary is `DatabaseConnection.execute_query`, which validates before invoking the cursor. `execute_non_query` is disabled and raises `SQLSecurityError`.

Security decisions are structured as `SecurityDecision(valid, reason_code, message, statement_type, statement_count, ast)`. No LLM, prompt, database call or SQL rewrite participates in the decision.
