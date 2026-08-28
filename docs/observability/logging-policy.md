# Logging Policy

Logs are JSON objects with `timestamp`, `level`, `event`, `component`, and request-local `trace_id` (plus `session_id` when known). `LOG_LEVEL` controls console verbosity and defaults safely to `INFO`; `LOG_DIR` defaults to `logs`. File logs are `logs/app.log`, rotated at 10 MiB with five backups.

Use `log_event()` for new instrumentation. Event names are stable snake_case values. User-controlled strings are serialized as JSON, so newlines and tabs cannot create forged records. Invalid log levels fall back to `INFO`.

Normal lifecycle events are `INFO`; validation rejection is `WARNING`; database/LLM/application failures are `ERROR`. Raw exception messages are not logged at instrumented boundaries.
