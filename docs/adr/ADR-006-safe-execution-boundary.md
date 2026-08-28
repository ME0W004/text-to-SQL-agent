# ADR-006: Safe Execution Boundary

Status: Accepted. QueryExecutor is the only DB boundary and enforces read-only credentials/session, timeout, row/column caps and request isolation.

