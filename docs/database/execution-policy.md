# Execution Policy

`ExecutionPolicy` is the single configuration contract for database resource limits. Construction validates every value as a positive integer and fails closed when configuration is invalid.

| Setting | Environment variable | Default | Meaning |
|---|---|---:|---|
| `max_rows` | `DB_MAX_ROWS` | 1000 | Largest successful result set |
| `max_fetch_batch` | `DB_MAX_FETCH_BATCH` | 100 | Largest requested `fetchmany()` batch |
| `query_timeout` | `DB_QUERY_TIMEOUT` | 30 seconds | `pymssql` command/query timeout |
| `connection_timeout` | `DB_CONNECTION_TIMEOUT` | 5 seconds | `pymssql` login timeout |

There is no unlimited fallback. Empty variables use the documented safe defaults; malformed, zero, negative, boolean, or otherwise invalid values reject policy construction.

## Row-limit semantics

The behavior is **fail**, not truncate or paginate. The executor fetches at most `max_rows + 1` rows. Results with zero through `max_rows` rows succeed. Seeing the extra row closes the resources and raises `DATABASE_RESULT_LIMIT`; partial rows are not returned. SQL is never rewritten, so query meaning and Prompt 2 security decisions remain unchanged.
