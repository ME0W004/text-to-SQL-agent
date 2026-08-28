# Current Architecture

Inspected code at 2026-08-26; code is authoritative where README or older reports differ. Sprint 0 changes documentation only.

| Layer | Components | Evidence |
|---|---|---|
| Presentation | src/web/app.py, run.py | Streamlit calls /api/v1/ask; CLI calls agent directly. |
| Application | src/api/routes.py, src/agents/text_to_sql_agent.py | FastAPI singleton agent orchestrates sessions, extraction, DB, rows and answers. |
| Core | src/core/sql_extractor, prompts.py, core/llm, schema_hash.py, column_mapper_updater.py | Prompting, OpenAI generation, cleaning, validation and map refresh. |
| Data | pymssql DatabaseConnection; SQLite MemoryManager; static models/COLUMN_MAP | Shared cursor, fetchall, session/message persistence and static metadata. |

Active runtime uses only OpenAIClient through LLMClient. Groq, Gemini and Ollama adapters are not wired. SQL now passes through deterministic TSQL AST single-statement/read-only checks in the extractor compatibility validator and again before the database cursor. There is no separate question understanding, schema retrieval, planning, semantic validation, complexity guard, result analysis, presentation planner, answer validator or routing. Agent construction connects to MSSQL and may spawn map-generation code. FastAPI handlers are async but call blocking DB/HTTP synchronously.
