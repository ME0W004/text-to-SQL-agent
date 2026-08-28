# Trace Context

`TraceContext` contains a single `trace_id` and optional `session_id`, stored in a Python `contextvars.ContextVar`. FastAPI middleware accepts a valid `X-Trace-ID` or generates a new UUID; the same ID is returned in the response header. Invalid or injection-prone inbound IDs are replaced. The agent creates a context for direct/CLI calls when none exists, and resets it after completion.

Context variables are task-local, so concurrent async requests do not mix trace IDs. Session IDs may be updated after the agent creates a new session without changing the trace ID.
