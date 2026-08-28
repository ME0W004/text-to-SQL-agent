# Memory Audit

> Scope: `src/memory/manager.py` and its usage by `TextToSQLAgent`. Runtime facts from live inspection of `data/conversations.db` (14 sessions / 48 messages at inspection time).

---

## 1. Storage schema (actual)

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
```
(`manager.py:18-35`)

Observed DB state: tables match code exactly; `sqlite_sequence` present; **only index is the autoindex on `sessions.id`.** The intended `idx_messages_session(session_id, created_at)` creation was commented out before commit (`manager.py:36-41`) — every history query full-scans messages.

## 2. Session lifecycle

| Step | Behavior | Code |
|---|---|---|
| Creation | `create_session()` inserts `uuid4()` string | `manager.py:43-50` |
| Resolution in `ask` | no id → create; provided id not found → **silently discard and create new** | `text_to_sql_agent.py:66-70` |
| Update | each message insert bumps `sessions.updated_at` | `manager.py:58-61` |
| Validation | `session_exists` used only for the replace decision | `manager.py:80-86` |
| Deletion | `clear_session()` exists but has **zero callers**; UI "Start New Session" only resets client state → orphaned server-side sessions accumulate forever | `manager.py:75-78`, `app.py:187-190` |
| Expiry/TTL/cleanup | none exists | — |

CLI note: `run.py:16` never passes a session_id ⇒ every CLI question creates a fresh session; conversation memory is effectively unused from the CLI.

## 3. Persistence behavior

- `add_message` opens a **new SQLite connection per call**; one turn = 2 connections (user msg, assistant msg) + 2 UPDATE statements. No transaction wraps the user+assistant pair: a crash between them leaves an unanswered user message.
- Empty-result turns persist the fixed English sentence `"No data found for your query."` as the assistant content (`text_to_sql_agent.py:85-86`) — this text later re-enters prompts as "history".
- Error turns do NOT persist anything (persistence happens only on success path after answer) — actually verified: the two `add_message` calls sit inside the success path (lines 96–97); failure envelopes return early without memory writes.

## 4. Retrieval logic

- `get_history(sid, limit=5)`: `SELECT role, content … ORDER BY created_at DESC LIMIT ?` then Python `reversed()` (`manager.py:63-73`).
- Ordering relies solely on `created_at`, whose SQLite CURRENT_TIMESTAMP resolution is **1 second**. Live evidence of ties: 48 messages but only **24 distinct timestamps** — user+assistant pairs share seconds. Within a tie, row order is unspecified → history can present assistant-before-user or swap adjacent turns.
- Returned items carry only `{role, content}` — no ids/timestamps for downstream deduplication.
- Limit is hard-coded to 5 at the single call site (`text_to_sql_agent.py:74`).

## 5. Context construction & token growth

- History is injected verbatim into the SQL prompt after few-shots (`prompts.py:53-57`), including assistant Markdown answers with data tables → duplicated context (the same numbers appear as history AND as current-turn rows) and fast token burn under the 500-token cap.
- No token accounting/truncation strategy exists anywhere; growth is bounded today only by the limit-5 rule and small answers.

## 6. Concurrency considerations

- API runs a single shared agent ⇒ all requests share one `MemoryManager`; sqlite3 connections are opened per call without `check_same_thread` concerns (fresh per call), but concurrent writes rely on SQLite default locking; no WAL mode, no busy_timeout set → rare `database is locked` failures possible under concurrency.
- No uniqueness/idempotency guard against duplicate inserts for retried requests.

## 7. Weakness register

| # | Weakness | Evidence |
|---|---|---|
| M1 | Missing index on `(session_id, created_at)` despite being planned | commented block `manager.py:36-41` |
| M2 | Same-second ordering ambiguity can corrupt turn order | live DB: 24 distinct seconds / 48 messages |
| M3 | Sessions/messages never cleaned; UI reset leaks server state | zero callers of `clear_session`; `app.py` sidebar |
| M4 | Silent session replacement hides client bugs | agent lines 69–70 |
| M5 | No FK enforcement (PRAGMA off by default); orphan messages possible if session deleted while messages remain (clear_session deletes both, but ordering inside one transaction not guaranteed across two statements—two executes in one connection/implicit tx, acceptable) | schema DDL |
| M6 | Pair-insert not atomic (two connections) | `ask` flow |
| M7 | History includes bulky Markdown answers → duplication + token waste | prompt construction |
| M8 | CLI creates a session per question — memory feature dead on that path | `run.py:16` |
| M9 | Relative DB path `data/conversations.db` depends on process CWD | `manager.py:9` |
| M10 | No pagination/export tooling; nothing surfaces stored conversations for debugging | — |
