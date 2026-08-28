# Database Execution Audit — Sprint 1 Prompt 3

## Previous behavior

`DatabaseConnection` stored one mutable `connection` and one mutable `cursor` on a singleton-like agent instance. `execute_query()` reused them, called unbounded `fetchall()`, used a hard-coded `timeout=5` without distinguishing login and command timeout, logged and re-raised raw exceptions, and did not close per-query resources. Concurrent API requests could therefore operate on the same cursor and connection state.

Security validation was correctly moved ahead of connection creation in Prompt 2. `execute_non_query()` was already disabled fail-closed.

## Risks found

- Unbounded result materialization and process-memory growth.
- Shared cursor contamination between simultaneous requests.
- Shared connection/transaction/session state across requests.
- Raw `pymssql` exceptions crossing the database boundary.
- Incomplete cleanup after query, cursor, timeout, or connection failures.
- Connection timeout and query timeout were not separately configured.
- Startup schema inspection uses the explicit legacy `connect()` connection; request execution previously reused that resource.

## Remediation

Request queries now use a fresh connection and dictionary cursor per call, after security approval. Retrieval uses bounded `fetchmany()` and reads at most `max_rows + 1`. Every exit path closes both resources. Driver errors become typed application database errors. The explicit `connect()` method remains only for startup schema inspection, creates no shared cursor, and the agent closes it immediately after initialization.

## Residual observations

The startup schema hash utility still uses its own `fetchall()` for a fixed `INFORMATION_SCHEMA` query covering three known tables. It is not an end-user SQL execution path. Connection pooling is deliberately deferred; it was not needed to remove unsafe sharing.
