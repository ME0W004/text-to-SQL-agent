# Sprint 1 Integration Map

## Production request path

```text
FastAPI middleware
  → TraceContext (contextvars)
  → /api/v1/ask
  → TextToSQLAgent.ask
  → SQLExtractor.extract (LLM + SQLValidator compatibility)
  → DatabaseConnection.execute_query
      → SQLSecurityValidator
      → ExecutionPolicy
      → isolated pymssql connection/cursor
      → bounded result
  → response LLM generation
  → API envelope + X-Trace-ID
```

| Integration point | Input/output | Errors | Trace/correlation | Compatibility |
|---|---|---|---|---|
| API → Agent | `AskRequest` → existing `dict` envelope | Agent catches typed validation/database errors | middleware context; session updated by agent | request/response models unchanged; response adds only header |
| Agent → Extractor | question/history → SQL string | validation and provider errors remain bounded | inherited context | `ask()` signature unchanged |
| Extractor → Security | candidate SQL → accepted/retry/rejection | legacy `SQLValidationError` | SQL fingerprint only in events | no SQL rewrite |
| Agent → Database | validated SQL → `list[dict]` | `DatabaseError` codes; raw driver hidden | inherited context; duration/fingerprint events | `execute_query() -> list[dict]` unchanged |
| Evaluation → Agent | canonical case → normalized `EvaluationResponse` | adapter isolates each case | agent creates/propagates trace | adapter uses public `ask()` only |
| CLI → Agent | input question → existing terminal output | existing UX retained | one trace per question | no argument/interface change |

## Boundary invariants

- No normal production DB path bypasses SQL security or execution policy.
- Security rejection occurs before driver connection creation.
- Execution policy construction rejects invalid values; no unlimited fallback exists.
- Structured events carry IDs, fingerprints, timings, and typed codes, not raw SQL, rows, prompts, credentials, or driver messages.
- No feature flags were added: security and safe execution are mandatory in production; structured logging is centrally configured and safe by default.
