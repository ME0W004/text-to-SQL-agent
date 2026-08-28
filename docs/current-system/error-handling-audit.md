# Error Handling Audit

> What actually happens when things fail, traced through `routes.py`, `text_to_sql_agent.py`, `extractor.py`, `validator.py`, `connection.py`, `client.py`, `manager.py`, and observed in `logs/`.

---

## 1. Custom exceptions inventory

| Exception | Defined | Actually raised? | Actually caught where it matters? |
|---|---|---|---|
| `TextToSQLException` + subclasses (`ConfigurationError`, `DatabaseConnectionError`, `QueryExecutionError`, `SQLValidationError`, `LLMError`) | `src/utils/exceptions.py` | **No — none of them is ever raised** | Only `QueryExecutionError` imported by agent (line 13); its except-branch is dead code |
| `ConfigurationError` (duplicate!) | also defined locally in `config_loader.py:6` | Yes — raised there for missing env vars | Not caught anywhere; propagates to process start / request thread |
| `SQLValidationError` (the live one) | `sql_extractor/validator.py:8` | Yes — validator + re-raised by extractor after failed repairs | Agent lines 109–117 |

Two parallel exception systems exist; the intended hierarchy is decorative.

## 2. Error boundaries by layer

### API boundary (`routes.py`)
- `/ask`: catches **everything**, logs traceback, returns HTTP 200 with `success=false` envelope (explicit design comment, lines 27–47). Clients cannot distinguish 4xx/5xx classes.
- `/health`: catches everything → status "error" payload.
- Startup-time failures inside lazy agent construction surface through this same catch (e.g., missing env vars, DB down).

### Agent boundary (`text_to_sql_agent.ask`)
- `SQLValidationError` → envelope `error="Invalid SQL: …"` (+ sql if attached).
- `QueryExecutionError` → dead branch (never raised).
- Generic `Exception` → envelope `error=f"Internal error: {e}"`; full traceback logged. Raw pymssql errors land here — user sees e.g. `(147, b'An aggregate may not appear…')`.
- Note: memory writes happen before return but only on success path; on error nothing is persisted (silent loss of the question).

### Extraction boundary
- Validator rejection after final attempt → raise (agent handles).
- LLM failure (`RuntimeError` from facade) → generic branch. No distinction between provider outage vs bad key vs timeout.

### Database boundary
- `connect`/`execute_query` log then re-raise raw driver exceptions. `execute_non_query` rolls back on error (unused code path).

### LLM boundary
- All clients wrap into `RuntimeError("… API error: {e}")`; facade adds its own RuntimeError variants for unavailable client/internet. Type information destroyed.

### Memory boundary
- No error handling at all: sqlite3 exceptions (locked db, disk full) propagate as generic errors mid-`ask` **after** SQL executed — user gets "Internal error" although the query succeeded.

## 3. Retry / fallback behavior

| Failure class | Retry | Fallback |
|---|---|---|
| Validation rejection | 1 corrective LLM regeneration (`extractor.py:50-69`) | none — final rejection surfaces |
| Execution (DB) error | none | none — despite README's «اصلاح خودکار» claim |
| LLM/provider error | SDK-default internal retries only | none (no alternate provider wired) |
| Internet check failure | none | hard failure even if proxy reachable |
| UI→API network | none | error box |

## 4. Observed real incidents in logs (evidence)

1. `OpenAI API error: Connection error.` + `httpx.ConnectError: getaddrinfo failed` chains → surfaced as "Unexpected error" envelopes (`logs/app_20260823.log`).
2. pymssql error 147 aggregate-in-WHERE → raw DB message shown to user, no repair (`logs/app_20260821.log`).
3. Historical `OllamaClient.__init__() got an unexpected keyword argument 'max_tokens'` warning — a broken fallback init that used to degrade startup (`logs/app_20260822.log`).
4. Repeated `Missing translation for column` warnings (2,510×/day) — degraded presentation rather than raised errors.

## 5. User-facing error quality

| Class | What user sees |
|---|---|
| Dangerous SQL intent | "Invalid SQL: Query contains forbidden keyword: DELETE" (+ rejected SQL shown) — good transparency |
| Syntax-invalid SQL after repair | "Invalid SQL: SQL syntax error: …" (sqlglot text, English, technical) |
| Bad column/table hallucination | raw pymssql bytes-decoded message e.g. `(207, b'Invalid column name …')` |
| Provider outage | "Internal error: OpenAI generation failed: OpenAI API error: Connection error." |
| Missing config | ConfigurationError string at first request; HTTP 200 envelope |
| API unreachable | friendly Streamlit connection message |

All technical messages are English inside an otherwise Persian product; none are localized or action-guiding.

## 6. Failure taxonomy (current coverage)

| Category | Handled? | Notes |
|---|---|---|
| Configuration | Partial | raised clearly, not caught centrally; duplicate exception types |
| Network (LLM/proxy) | Detected, not recovered | internet check false-negatives possible |
| Provider (auth/quota/model) | Detected via generic wrap | indistinguishable from network |
| Parsing (LLM output) | Recovered once via repair loop | context-starved repair |
| Validation (security/syntax) | Enforced pre-execution | bypassable multi-statement gap documented elsewhere |
| Database (syntax/timeout/constraints) | Surfaced raw | no repair, no mapping to friendly text |
| Semantic (wrong-but-valid SQL) | **Undetected** | silent wrong answers |
| Presentation (translation/format) | Degraded silently | warnings only |
| Memory (sqlite) | Unhandled | post-success failures mislead users |

## 7. Logging behavior on errors

- Agent logs ERROR + full traceback for generic failures (good), one line for validation failures.
- `connection.py` uses stdlib root-ish logging with `basicConfig(INFO)` at import — can double-print alongside `utils.logger` handlers.
- No correlation ids: multiple concurrent requests interleave log lines unrecoverably.
