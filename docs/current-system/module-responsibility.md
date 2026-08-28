# Module Responsibility Map

> One section per important module. All paths relative to repo root. Line numbers refer to the code at commit `e6b53d6`.

---

## 1. `src/agents/text_to_sql_agent.py` — `TextToSQLAgent`

| Aspect | Detail |
|---|---|
| Responsibility | End-to-end orchestration: wiring, sessions, SQL generation, execution, filtering/translation, answer generation, persistence, error mapping. |
| Public class | `TextToSQLAgent` |
| Public methods | `__init__`, `ask(question, session_id=None) -> dict`, `close()`; internal `_filter_and_translate_rows(rows)` |
| Dependencies | `DatabaseConnection`, `SQLExtractor`, `LLMClient`, `SystemPrompts`, `MemoryManager`, `load_config`, `ensure_column_map_is_updated`, module-level `COLUMN_MAP` (guarded import, lines 17–20), `QueryExecutionError` (imported, never actually raised by anything) |
| Callers | `src/api/routes.py:get_agent()`, `run.py:main()` |
| Inputs | natural-language question (str), optional session_id |
| Outputs | result dict `{question, sql, answer, row_count, rows, success, session_id}` or error variant |
| Side effects | DB connection opened in constructor; possible subprocess regeneration of source files at startup; writes to SQLite memory; LLM network calls; WARNING logs for missing translations |
| Implementation details | History limited to last 5 messages (`ask`, line 74). Rows filtered/translated **before** answer generation, so the answer LLM sees Persian headers. Empty rows → fixed English string (85–86), still persisted as assistant message. Unknown session_id silently replaced with a new session (69–70). |
| Known weaknesses | God-object (6 responsibilities); swallows unknown session ids; drops *all* `*Id` columns including meaningful ones (e.g. `buyerId`); translation applied before answer generation can confuse numeric interpretation less but hides raw column names from the answer step; no logging of question/session/model; `except QueryExecutionError` branch is dead code because nothing raises it (119–128). |

**Verdict: too many responsibilities — this is the main refactor target.**

---

## 2. `src/core/prompts.py` — `SystemPrompts`

| Aspect | Detail |
|---|---|
| Responsibility | Build all three prompt strings (SQL generation, natural response, error correction). |
| Public class | `SystemPrompts` (all `@staticmethod`) |
| Public functions | `sql_generation_prompt(question, history=None)`, `natural_response_prompt(question, rows, row_count)`, `error_correction_prompt(question, error_message, invalid_sql)` |
| Dependencies | `FULL_SCHEMA` (`database/models.py`), `FEW_SHOT_EXAMPLES` (`sql_extractor/few_shots.py`) |
| Callers | `SQLExtractor.extract` (generation + correction), `TextToSQLAgent.ask` (natural response) |
| Inputs / outputs | question/history/rows → prompt strings |
| Side effects | none |
| Details | See dedicated `prompt-audit.md`. Notable: history appended verbatim after few-shots (53–57); response prompt embeds rows via Python dict repr (`str(row)`, line 69); correction prompt omits schema and rules (91–108). |
| Weaknesses | Monolithic string templates; duplicated rule knowledge that also lives in `models.py`; English headings demanded for a Persian answer; no output-format contract beyond prose instructions. |

---

## 3. `src/core/database/connection.py` — `DatabaseConnection`

| Aspect | Detail |
|---|---|
| Responsibility | Open/manage one pymssql connection + cursor; run queries. |
| Public class/methods | `DatabaseConnection(host, port, user, password, database=None)`, `connect(database=None)`, `close()`, `execute_query(query, params=None) -> list[dict]`, `execute_non_query(query, params=None) -> bool` |
| Dependencies | `pymssql`, stdlib `logging` (uses its own basicConfig, not `utils.logger`) |
| Callers | `TextToSQLAgent.__init__`, `scripts/generate_column_map.py:main`; `column_mapper_updater`/`schema_hash` use the raw connection object it exposes (`self.connection`, `.cursor()`) |
| Inputs | config kwargs; raw SQL strings |
| Outputs | list of dict rows (as_dict=True) |
| Side effects | opens TCP connection; `logging.basicConfig(level=INFO)` at import time (line 6) mutates root logger globally |
| Details | `connect(timeout=5, charset='UTF-8')` — per pymssql docs this `timeout` is the **query wait timeout**, not just login; single shared cursor reused across calls; always `fetchall()` unbounded; `execute_non_query` commits/rollbacks but is dead code today. |
| Weaknesses | No pool, no reconnect logic, no per-call timeout parameter, no read-only guard at driver level, params accepted but never used by callers (LLM path is string interpolation upstream), exceptions re-raised raw (no wrapping into `utils.exceptions`). |

---

## 4. `src/core/database/models.py`

| Aspect | Detail |
|---|---|
| Responsibility | Hard-coded schema knowledge for prompts and (nominally) code usage. |
| Public symbols | `ORDERS_MODEL`, `ORDER_ITEMS_MODEL`, `SHIPMENTS_MODEL`, `build_compact_schema()`, `FULL_SCHEMA`, `ALL_MODELS` |
| Used in practice | Only `FULL_SCHEMA` (imported by `core/prompts.py:3`). Everything else is dead exports. |
| Weaknesses | `FULL_SCHEMA` documents only `Sale.Orders` and `Sale.OrderItems` — `Sale.Shipments` columns are missing (only relationship note present, 120–122). Claims `buyerId` FKs to a "Buyers table" that does not exist in the DB. Column semantics ("quantity in KG", "unitPrice RLS") are assumptions not enforced anywhere. Static text duplicates what `INFORMATION_SCHEMA` could provide. |

---

## 5. `src/core/sql_extractor/extractor.py` — `SQLExtractor`

| Aspect | Detail |
|---|---|
| Responsibility | Question (+history) → validated SQL via LLM with repair loop. |
| Public class/methods | `SQLExtractor(llm_client)`, `.extract(question, history=None, max_retries=2) -> str`, internal `_clean_sql(raw_sql)` |
| Dependencies | `SystemPrompts`, `LLMClient` (type hint only), `SQLValidator`, `SQLValidationError` |
| Callers | `TextToSQLAgent.ask` |
| Inputs | question, history list of `{role, content}` |
| Outputs | SQL string (validated or final-attempt) |
| Side effects | up to 2 LLM calls; WARNING/DEBUG logs on repair |
| Details | `_clean_sql`: strips ``` fences; regex `(WITH|SELECT)\s+` cuts everything before first match — this can silently mutate non-SELECT statements into SELECT fragments (e.g., `INSERT ... SELECT` loses its prefix); rstrip(';'). Repair loop: initial generation + at most 1 corrective regeneration (loop runs while `attempt < max_retries` where `max_retries=2`). |
| Weaknesses | Cleaning is lossy and can alter statement type; repair prompt lacks schema/rules/few-shots; no logging of which attempt succeeded; returns last invalid SQL only implicitly via raised exception; no handling of empty cleaned SQL before validate (validator raises "empty" — fine but consumes a retry). |

---

## 6. `src/core/sql_extractor/validator.py` — `SQLValidator`

| Aspect | Detail |
|---|---|
| Responsibility | Reject unsafe/invalid SQL before execution. |
| Public symbols | `SQLValidationError(message, sql=None)` (the exception used app-wide), `SQLValidator.validate(sql) -> bool` |
| Checks performed | (1) non-empty; (2) starts with SELECT/WITH; (3) word-boundary blocklist DROP/DELETE/UPDATE/INSERT/ALTER/CREATE/EXEC/EXECUTE; (4) `sqlglot.parse_one(dialect="tsql")` parses |
| Callers | `SQLExtractor.extract` |
| Weaknesses (verified empirically during Sprint 0) | Multi-statement strings parse successfully (sqlglot `parse_one` accepts `SELECT 1; TRUNCATE TABLE Sale.Orders` without error) and keywords like TRUNCATE/GRANT/MERGE/BACKUP/RESTORE/SHUTDOWN/KILL/OPENROWSET/OPENDATASOURCE/xp_cmdshell/sp_* are not blocked → trailing destructive statements would be forwarded to `cursor.execute`. Blocklist also over-blocks benign identifiers/comments containing those words. No AST-based statement-count check, no schema/table allowlist, no LIMIT/cost guard. |

---

## 7. `src/core/sql_extractor/few_shots.py`

Static list `FEW_SHOT_EXAMPLES` of 5 examples (Persian question + T-SQL). Used only inside `sql_generation_prompt`. Weaknesses: all examples target Orders×OrderItems revenue patterns; example year `'1403'` does not exist in data (years present: 1394–1399) teaching an empty-result pattern; none exercise Shipments despite rules mentioning them.

---

## 8. `src/core/llm/*`

| Module | Status | Notes |
|---|---|---|
| `base.py` `BaseLLMClient` ABC | Dead abstraction | Only `OllamaClient` implements it; `OpenAIClient`/`GeminiClient`/`GroqClient` do not. |
| `client.py` `LLMClient` | **The used facade** | Wraps OpenAI only; pre-pings `https://api.openai.com` before every call (26–32); raises `RuntimeError` when unavailable; temperature 0.1 / max_tokens 500 hardcoded here (19–22). No fallback chain despite multi-client file layout. |
| `openai_client.py` `OpenAIClient` | Used | `gpt-4o-mini`; honors `OPENAI_BASE_URL` override (line 19) — deployment uses a third-party proxy base URL; single user message; no timeout/retry configuration; wraps errors into RuntimeError. |
| `gemini_client.py` | Dead code | `gemini-2.5-flash`, temp 0.2, 500 tokens; no availability check; not wired anywhere. |
| `groq_client.py` | Dead code | `llama-3.3-70b-versatile` via langchain_groq; retries=2; not wired anywhere. |
| `ollama_client.py` | Dead code | `llama3.1:8b`; implements `is_available`; older runtime log shows a previous facade version tried `OllamaClient(max_tokens=...)` and crashed (TypeError) — evidence the fallback design existed and was removed/broken. |

---

## 9. `src/core/column_mapper.py` (generated) & `get_column_mapping`

Auto-generated dict mapping 41 English column names → Persian labels; plus unused helper `get_column_mapping`. Consumed by `text_to_sql_agent._filter_and_translate_rows` (module-level import) and re-exported by `core/__init__.py`.
Weaknesses: contains a **wrong translation** (`mesc` → «مقدار»/"amount"; actual meaning per `database/models.py` and data samples is material code); includes stale entry `delivered_quantity` (a past query alias); aliases produced by future queries will never exist here; regenerated file content depends on LLM output (non-deterministic).

---

## 10. `src/core/schema_hash.py` — `get_schema_hash(db_conn)`

Queries `INFORMATION_SCHEMA.COLUMNS` for the three Sale tables (name/type/nullable, ordinal order), concatenates to a pipe-delimited string, MD5-hashes it. Input: raw pymssql connection object. Caller: `column_mapper_updater`. Weaknesses: hash covers only 3 hard-coded tables; MD5 fine for change detection but brittle if ordering changes; ignores indexes/constraints (none exist anyway).

---

## 11. `src/core/column_mapper_updater.py`

| Aspect | Detail |
|---|---|
| Responsibility | On startup: compare current schema hash to cached hash; regenerate column map if changed. |
| Public function | `ensure_column_map_is_updated(db_conn: DatabaseConnection) -> bool` |
| Internals | `_load_cache/_save_cache` on `.schema_cache.json`; `_run_column_map_generator` spawns `[sys.executable, scripts/generate_column_map.py]` with `cwd=os.path.dirname(script_path)` and `timeout=30` (lines 40–47) |
| Callers | `TextToSQLAgent.__init__` |
| Side effects | subprocess launch; potential LLM usage; file writes; cache write |
| Critical defect | Because the generator resolves its output path relative to CWD (`generate_column_map.py:108` uses `os.path.join("src","core","column_mapper.py")`) and the updater sets `cwd=scripts/`, auto-regeneration writes **`scripts/src/core/column_mapper.py`**, not the imported module. Evidence: stray tracked file exists with different content than the active map. Auto-update therefore never updates the running system's map. Return value ignored by caller. |

---

## 12. `scripts/generate_column_map.py`

Standalone generator. Fetches columns via INFORMATION_SCHEMA (tuples, ordinal order), chunks names 15-per-request, prompts LLM for strict JSON translation, regex-extracts `{...}`, merges chunks, fills failures with identity mapping, then **writes Python source** to cwd-relative `src/core/column_mapper.py`. Weaknesses: LLM-dependent correctness; silent identity fallback; wrong-path behavior under updater invocation; duplicates schema knowledge; no validation that translations differ from keys.

---

## 13. `src/memory/manager.py` — `MemoryManager`

| Aspect | Detail |
|---|---|
| Responsibility | Persist/retrieve conversation sessions. |
| Public methods | `create_session() -> uuid4 str`, `add_message(session_id, role, content)`, `get_history(session_id, limit=5) -> [{role,content}]`, `clear_session(session_id)` (never called), `session_exists(session_id)` |
| Storage | SQLite `data/conversations.db`; tables `sessions(id TEXT PK, created_at, updated_at)`, `messages(id INTEGER PK AUTOINCREMENT, session_id, role, content, created_at)`; FK declared, enforcement off; index creation commented out (36–41) |
| Callers | `TextToSQLAgent.ask` |
| Side effects | creates directories/db file; opens a new connection per operation |
| Weaknesses | Ordering relies solely on `created_at` (second precision) — user+assistant messages written in the same second tie arbitrarily (observed in live DB: 48 messages / 24 distinct seconds); two separate connections per turn (user insert + assistant insert + session update); no transaction around pair-insert; no pruning/TTL; history returned without ids/timestamps; no token budgeting. Full detail in `memory-audit.md`. |

---

## 14. `src/api/routes.py` + `main.py` + `models.py`

| Aspect | Detail |
|---|---|
| Responsibilities | HTTP surface. `routes.py`: lazy global agent; `/ask` maps any exception to HTTP 200 envelope; `/health` reports DB connection truthiness + LLM availability (which performs a live internet ping). `main.py`: CORS wildcard+credentials; root info. `models.py`: loose Pydantic models. |
| Weaknesses | Blocking sync work inside `async def` handlers stalls the event loop; singleton shared across requests (thread-safety of pymssql cursor + sqlite connections unmanaged); `AskResponse.rows` optional but UI assumes list; health endpoint triggers side-effectful agent construction; no auth/rate limiting; no request IDs. |

---

## 15. `src/web/app.py` — Streamlit UI

Chat UI with RTL CSS injection (58–177), sidebar session reset (184–201), chat rendering incl. DataFrame + SQL expander (204–228), send flow (232–336): posts `{question, session_id}` to `{API_URL}/ask` with 60 s timeout; renders answer markdown with `unsafe_allow_html=True`; appends assistant message with rows/sql; error path renders st.error plus offending SQL.
Weaknesses: XSS surface via model/data-driven HTML; duplicate table presentation (answer Markdown table AND `st.dataframe`); leftover `print()` debug statements; server-side sessions orphaned on "Start New Session"; no loading state persisted in history; API_URL from env only.

---

## 16. `src/utils/*`

| Module | Notes |
|---|---|
| `config_loader.py` | Maps 5 DB env vars; int conversion of DB_PORT unguarded (raw ValueError, not `ConfigurationError`); defines its own `ConfigurationError` duplicating `utils.exceptions.ConfigurationError`. |
| `exceptions.py` | Intended hierarchy; effectively dead. `QueryExecutionError` imported by the agent but never raised anywhere → misleading except-clause. Duplicate `SQLValidationError` concept vs validator's. |
| `logger.py` | Per-module logger; console INFO + file DEBUG to `logs/app_YYYYMMDD.log`; handler duplication guarded by `if not logger.handlers`; date-based file means long-lived processes keep writing to the old day's file. |

---

## Modules with too many responsibilities (explicit)

1. **`src/agents/text_to_sql_agent.py`** — wiring + orchestration + session mgmt + presentation transforms + persistence + error mapping.
2. **`src/core/prompts.py`** — three unrelated prompt domains in one static class with embedded schema/rules duplication.
3. **`scripts/generate_column_map.py`** — DB introspection + LLM prompting + JSON parsing + code emission in one script.
