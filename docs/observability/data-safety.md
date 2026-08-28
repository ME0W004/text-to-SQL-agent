# Observability Data Safety

Secret-like keys (`password`, `api_key`, `token`, `secret`, `authorization`, cookies, credentials, and connection strings) are recursively replaced with `[REDACTED]`. Private payload keys (`sql`, `question`, `answer`, `prompt`, `response`, `rows`, and `results`) are replaced with `[PAYLOAD_REDACTED]` by default. SQL identification uses lowercase whitespace-normalized SHA-256 fingerprints.

Raw database rows, LLM prompts/responses, credentials, API keys, authorization headers, and customer/financial data are therefore excluded from structured events. This policy is defense in depth; application memory and response payloads are outside log storage but still require deployment access controls.

Live distributed tracing, metrics, token accounting, and external retention infrastructure are intentionally not implemented in this foundation sprint.
