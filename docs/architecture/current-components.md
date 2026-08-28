# Current Components Inventory

| Component | Location | Responsibility | Inputs/outputs | Limitations | Status |
|---|---|---|---|---|---|
| Agent | src/agents/text_to_sql_agent.py | End-to-end orchestration and row filtering | question/session -> result dict | Mixed responsibilities; shared state | Split |
| SQLExtractor | src/core/sql_extractor/extractor.py | Prompt, clean SQL, retry validation errors | question/history -> SQL | Lossy cleaner; one repair; no execution repair | Refactor |
| SQLValidator | src/core/sql_extractor/validator.py + src/core/security/sql_security.py | TSQL AST parsing, single-statement and read-only policy | SQL -> bool/error or structured decision | No schema, semantic or cost checks | Refactor incrementally |
| LLMClient | src/core/llm/client.py | OpenAI availability and generation | prompt -> text | One provider; network probe per call | Refactor |
| Prompts | src/core/prompts.py | SQL, correction and answer templates | context -> prompt | Mixed concerns; static schema; answer sees 5 rows | Refactor |
| DatabaseConnection | src/core/database/connection.py | pymssql query execution behind AST security check | SQL -> list of dicts | Shared cursor; unbounded fetchall; raw DB errors | Refactor boundary |
| MemoryManager | src/memory/manager.py | SQLite sessions/messages/history | turns <-> SQLite | Last 5; timestamp ties; no retention/index | Refactor |
| Schema/map | src/core/database/models.py, schema_hash.py, column_mapper*.py | Static schema, MD5 and Persian labels | metadata -> strings | Drift; Shipments missing in FULL_SCHEMA; path bug | Replace |
| API/UI | src/api/*, src/web/app.py | HTTP envelope and RTL client | JSON <-> UI | Global singleton; unsafe HTML; duplicate/unbounded rendering | Refactor |
| Config/logging | src/utils/* | env, logs and exceptions | env/events -> config/logs | Duplicate exception classes; no trace IDs | Refactor |
