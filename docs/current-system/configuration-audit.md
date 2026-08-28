# Configuration & Dependency Audit

---

## 1. Environment variables (complete inventory)

| Variable | Read by | Required | Purpose |
|---|---|---|---|
| `DB_HOST` | `config_loader.load_config` | yes | MSSQL server |
| `DB_PORT` | same | yes | int-converted (raw ValueError if non-numeric) |
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | same | yes | credentials/db |
| `OPENAI_API_KEY` | `llm.client._init_client` (gate), `openai_client` (use) | de-facto yes for any LLM feature | provider key |
| `OPENAI_BASE_URL` | `openai_client:19` | no | proxy override — deployment uses third-party relay (`https://api.gapgpt.app/v1` per `.env.example`) |
| `GOOGLE_API_KEY` | `gemini_client` | only if Gemini used (never wired) | dead config |
| `GROQ_API_KEY` | `groq_client` | only if Groq used (never wired) | dead config |
| `API_URL` | `web/app.py:18` | no (default localhost:8000/api/v1) | UI→API endpoint |

Loading behavior: `load_dotenv()` is called independently in ≥6 modules (`openai_client`, `gemini_client`, `groq_client`, `web/app`, `config_loader.load_config`, plus scripts) — idempotent but scattered. There is **no central settings object**; consumers read `os.getenv` directly.

Secrets handling: `.env` gitignored (verified via `git ls-files`); values never logged by the app. Exception: `.env.example` is committed and contains concrete-looking infrastructure identifiers (public DB host, username, DB name, real relay URL) instead of placeholders — flagged as a security hygiene issue; secrets themselves are masked in that file.

## 2. Runtime configuration assumptions

| Assumption | Where enforced | Risk |
|---|---|---|
| Working directory = repo root | `MemoryManager("data/conversations.db")`, logger `"logs/"`, updater cache path, generator output path | running from another CWD scatters/misses files |
| Port 8000 free; reload enabled | `run_api.py` hard-coded host/port/reload | dev server assumed in prod-like use |
| Streamlit on 8501 | `run_ui.py` argv override | — |
| Single API process | module-global agent singleton + shared pymssql cursor | multi-worker uvicorn would multiply agents/connections silently |

## 3. Dependencies: declared vs actually used

From `pyproject.toml [project.dependencies]` vs import scan of `src/`, `scripts/`, entry points:

| Package | Declared | Imported anywhere? | Verdict |
|---|---|---|---|
| fastapi, uvicorn | ✔ | api/main, run_api | used |
| streamlit | ✔ | web/app | used |
| pandas | ✔ | web/app | used (UI only) |
| requests | ✔ | web/app, llm/client | used |
| python-dotenv | ✔ | many modules | used |
| pymssql | ✔ | database/connection | used — the actual DB driver |
| openai | ✔ | llm/openai_client | used |
| sqlglot | ✔ | sql_extractor/validator | used |
| langchain-groq | ✔ | groq_client | **dead path** |
| langchain-ollama | ✔ | ollama_client | **dead path** |
| google-genai | ✔ | gemini_client | **dead path** |
| langchain (core meta-pkg), langchain-openai | ✔ | **nowhere** | unused entirely |
| sqlalchemy | ✔ | **nowhere** | unused (no ORM/engine usage) |
| pyodbc | ✔ | **nowhere** | unused driver |
| psycopg2-binary | ✔ | **nowhere** | PostgreSQL driver in a MSSQL-only project |
| pymysql | ✔ | **nowhere** | MySQL driver unused |
| prettytable | ✔ | **nowhere** | unused |
| markdown | ✔ | **nowhere** | unused (Streamlit renders markdown natively) |

Installed versions in `.venv` satisfy the declared ranges (spot-checked: fastapi 0.141.1, openai 3.3.1, sqlglot 30.17.0, streamlit 1.62.0, pandas 3.0.5, pymssql 2.3.13). `uv.lock` is consistent with `pyproject.toml`.

Net effect: ~9 runtime dependencies are carried (installed, locked, attack-surface) with zero imports.

## 4. Cross-source inconsistencies

1. **README vs code**: multi-provider fallback claim vs single-provider facade (details in discrepancies.md).
2. **`.env.example` vs README instructions**: README says "replace placeholders", file already contains concrete values — contradictory guidance.
3. **pyproject vs docs**: project described as SQL Server-only, yet three additional DB drivers shipped.
4. **`utils/exceptions.py` vs actual error flow**: hierarchy unused; duplicate `ConfigurationError` in `config_loader.py`; live `SQLValidationError` lives in validator.
5. **Python version pinning**: `.python-version`=3.11, `requires-python >=3.11` — consistent; no upper bound.
6. **No dev tooling configured**: no pytest/ruff/mypy/black config sections despite a `tests/` skeleton existing; tests cannot run meaningfully today (empty).

## 5. Configuration-related failure modes observed

- Missing env vars → `ConfigurationError` raised at first agent construction inside an HTTP request (HTTP 200 envelope, confusing timing).
- Non-int `DB_PORT` → unhandled ValueError.
- `OPENAI_API_KEY` absent → LLMClient has no client; every ask returns "OpenAI client is not available…" — system boots but is functionally dead without clear startup diagnostics.
- Schema change while API running → next request triggers subprocess+LLM regeneration inline (tens of seconds) or fails silently (updater return value ignored).
