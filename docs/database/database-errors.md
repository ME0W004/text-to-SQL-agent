# Database Error Contract

All database/application boundary failures use `DatabaseError` with one stable `DatabaseErrorCode`. Raw `pymssql` exceptions are retained only as the internal `cause` and are not included in the public message.

| Code | Meaning |
|---|---|
| `DATABASE_CONNECTION_ERROR` | Driver connection or login failure |
| `DATABASE_TIMEOUT` | Query or login timeout detected |
| `DATABASE_QUERY_ERROR` | SQL Server rejected/failed query execution |
| `DATABASE_PERMISSION_ERROR` | Driver reported access/authorization denial |
| `DATABASE_RESOURCE_LIMIT` | Reserved for non-row execution resource limits |
| `DATABASE_RESULT_LIMIT` | More than configured `max_rows` detected |
| `DATABASE_CURSOR_ERROR` | Cursor creation or result retrieval failure |
| `DATABASE_UNKNOWN_ERROR` | Unexpected failure not reliably classifiable |

Classification intentionally avoids extensive parsing of vendor text. Timeout and permission markers are narrow fallbacks because `pymssql` does not expose a dedicated timeout/permission exception class. Higher layers catch the existing `QueryExecutionError` base class for backward compatibility.
