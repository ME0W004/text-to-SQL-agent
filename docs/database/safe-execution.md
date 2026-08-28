# Safe Database Execution

The request path is:

```text
SQL
→ deterministic security validation
→ validated ExecutionPolicy
→ isolated pymssql connection and cursor
→ bounded fetchmany retrieval
→ list[dict] or typed DatabaseError
```

Security decides whether SQL is permitted. Execution policy controls how an accepted statement consumes resources. Neither layer rewrites SQL.

## Lifecycle and concurrency

Each `execute_query()` call creates its own connection and dictionary cursor. No request query uses the mutable compatibility connection held by `connect()`. Cursor and connection are closed in `finally` on success, result-limit failure, query error, timeout, cursor error, or unexpected application failure. Validation failure happens before any driver call.

This removes shared cursor/session state and provides request-level isolation without introducing speculative pooling. A timed-out connection is always discarded, not reused.

## Timeout behavior

`pymssql.connect(timeout=...)` supplies the FreeTDS query timeout and `login_timeout=...` separately controls connection/login. No application timer or thread cancellation is used. A driver timeout is mapped to `DATABASE_TIMEOUT`, and its connection is closed.

Driver limitation: unit tests can verify parameter propagation, mapping, and cleanup, but only a live SQL Server integration test can prove server/network-specific cancellation timing. FreeTDS timeout errors can vary by platform; classification uses the driver's exception type plus a minimal timeout marker check.

## Deployment requirement

The database principal should independently be read-only and least-privileged. Prompt 2 SQL policy is defense in depth, not a replacement for SQL Server permissions. Principal provisioning is outside this sprint.
