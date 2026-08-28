# Documentation ↔ Code Discrepancies

> Every case found where documentation, configuration files, or code comments disagree with actual behavior. Reported only — not resolved (per Sprint 0 rules).

---

## 1. README.md vs implementation

| # | README claim | Reality in code | Evidence |
|---|---|---|---|
| 1.1 | «پشتیبانی از چندین مدل زبانی (OpenAI، Groq، Gemini) با قابلیت Fallback خودکار» (multi-provider with automatic fallback) | Only OpenAI-compatible provider is wired; no fallback branch exists; Groq/Gemini/Ollama clients never instantiated | `src/core/llm/client.py:34-43`; grep: zero call sites |
| 1.2 | «اصلاح خودکار در صورت خطا» (auto-correction on error) | Auto-repair exists **only for validation rejections**; database execution errors are never repaired | `sql_extractor/extractor.py:50-69` vs log evidence of pymssql error surfaced raw |
| 1.3 | Troubleshooting row for `Ollama client init failed` implies Ollama participates in runtime | Ollama is dead code; historical log shows its init was attempted with wrong kwargs and abandoned | `ollama_client.py`; `logs/app_20260822.log` |
| 1.4 | Project-structure tree omits `src/agents/`, `src/memory/`, `src/schemas/`, `report.md`, `.schema_cache.json`, `logs/`, `data/` | These exist and are central (agent + memory especially) | repo tree |
| 1.5 | Startup auto-regenerates the Persian column map when schema changes («سیستم هنگام راه‌اندازی… به‌روز می‌کند») | The regeneration runs but writes to the **wrong path** (`scripts/src/core/column_mapper.py`) due to CWD handling; the imported map is unchanged; failure return ignored | `column_mapper_updater.py:40-47`, `generate_column_map.py:108`, stray tracked file ✅ |
| 1.6 | README structure lists `run_ui.py` as "alternative" UI launcher while main instructions use `streamlit run src/web/app.py` | Both work; minor inconsistency only | `run_ui.py` |

## 2. In-code documentation vs behavior

| # | Claim (in code) | Reality | Evidence |
|---|---|---|---|
| 2.1 | `FULL_SCHEMA`: "buyerId: Customer ID (Foreign Key to Buyers table, not shown)" | No Buyers table exists anywhere in the DB (single schema Sale, 3 tables); buyerId is a dangling int | live inspection: INFORMATION_SCHEMA + sys.foreign_keys empty |
| 2.2 | `FULL_SCHEMA` titled "Full schema with semantic descriptions" | Shipments table columns entirely missing from it (relationship note only) | `database/models.py:81-125` |
| 2.3 | `ORDERS_MODEL.foreign_keys = []` vs OrderItems/Shipments carrying FK entries | DB declares **no** FK constraints at all; all three entries are logical only | sys.foreign_keys empty |
| 2.4 | Agent imports `QueryExecutionError` and has an except-branch for it | Nothing ever raises it → dead branch; real DB errors fall through to generic handler | `text_to_sql_agent.py:13,119-128`; grep raises |
| 2.5 | `utils/exceptions.SQLValidationError` exists | The operative exception is `sql_extractor.validator.SQLValidationError`; the utils one is unused; two `ConfigurationError` classes exist (utils + config_loader) | both files |
| 2.6 | `memory/manager.py` comment: index creation "removed to avoid syntax issues… test separately" | Index simply doesn't exist; queries full-scan messages | commented block lines 36–41; sqlite_master shows autoindex only |
| 2.7 | `column_mapper.py` header "Do not edit manually. Run the script again to update." | The automatic update path is broken (1.5); manual edits are effectively the only working update mechanism today | updater cwd bug |
| 2.8 | API comment: errors returned with status 200 so Streamlit can display them | Implemented as described — but contradicts REST conventions implied by FastAPI response_model usage; noted as intentional deviation, not a bug per se | `routes.py:27-47` |

## 3. Configuration vs reality

| # | Item | Discrepancy |
|---|---|---|
| 3.1 | `.env.example` presented as a placeholder template (README instructs replacing values) | Contains concrete infrastructure values (public host IP, username, DB name, relay base URL) rather than placeholders — committed to git. Secrets masked. |
| 3.2 | Provider naming: docs/config say "OpenAI" | `OPENAI_BASE_URL` points to a third-party proxy (`api.gapgpt.app`) — the effective provider is a relay service; model behavior may differ from first-party OpenAI |
| 3.3 | pyproject ships PostgreSQL/MySQL/ODBC drivers (psycopg2, pymysql, pyodbc) and SQLAlchemy | Project is SQL-Server-only via pymssql; those drivers are imported nowhere |
| 3.4 | `langchain` meta-package and `langchain-openai` declared | Imported nowhere (only langchain-groq/langchain-ollama used by dead clients) |

## 4. report.md (manual test report) vs system behavior

| # | Report statement | Assessment against code/logs |
|---|---|---|
| 4.1 | DELETE-question correctly rejected by validation | Consistent with validator blocklist (`DELETE` blocked) ✔ |
| 4.2 | Analytical query success story uses `MONTH(o.entityDate)` grouping | Works, but bypasses the prompt's own rule 5 (operationMonth) — the report itself flags this; illustrates rule non-enforcement |
| 4.3 | Conclusion: system «آماده و قابل اعتماد» for production ("ready & trustworthy") | Contradicted by Sprint 0 findings (multi-statement bypass, semantic currency/unit mixing, broken column-map autoupdate, no tests) — see baseline-report |

## 5. Undocumented functionality that exists in code

- `DatabaseConnection.execute_non_query` (write path with commit/rollback) — present, tested nowhere, unused.
- `MemoryManager.clear_session` / `session_exists` (clear_session unused).
- `build_compact_schema`, `ORDERS_MODEL/ORDER_ITEMS_MODEL/SHIPMENTS_MODEL/ALL_MODELS` exports — unused.
- `run_ui.py` alternative launcher — absent from README's run instructions except a structure mention.
- Stray generated artifact `scripts/src/core/column_mapper.py` tracked in git (should not exist at that path).
- `delivered_quantity` entry inside active COLUMN_MAP — a fossil of a past runtime query alias, undocumented.

## 6. Architecture-diagram-level mismatches (docs-vs-code)

- Any diagram showing LLM fallback chains or provider routing depicts aspiration, not code.
- Any diagram implying schema validation before execution overstates reality (only syntax+keyword checks).
- Memory diagrams assuming session cleanup/expiry are aspirational.

Each item above is reported without modification, per sprint policy. Future sprints must decide explicitly which side (docs or code) is authoritative.
