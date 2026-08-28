# Observability Audit — Sprint 1 Prompt 4

Before this sprint, logging was split between per-module text loggers and root `basicConfig`. Files were date-pinned, unbounded, and had no rotation. SQL text, driver messages, and traceback strings could be emitted; request/session correlation and stage timing were absent. The API had no trace header and the CLI used `print()` for operational context.

The remediation is a single JSON formatter and `log_event()` API backed by request-local `contextvars`. Existing logger call sites remain compatible, while instrumented paths emit stable event names, trace/session IDs, durations, typed error codes, and SQL fingerprints. Secret-like fields and private payload fields are centrally redacted. File output uses a 10 MiB rotating handler with five backups.
