# Logging & Observability Audit

> Question answered: for a single user request, what can be reconstructed today from logs + stored data? Sources: `src/utils/logger.py`, log call sites, `logs/app_2026*.log` (3 files; the Aug 21 file has 2,584 lines), `data/conversations.db`.

---

## 1. Current instrumentation inventory

| Signal | Where produced | Level | Destination |
|---|---|---|---|
| Schema-hash check / column-map regeneration | `column_mapper_updater` | INFO/ERROR | console + daily file |
| Generated SQL (final validated string) | `text_to_sql_agent.ask:78` | INFO | console + file |
| Missing-translation warnings (deduped per process per column) | `_filter_and_translate_rows:56` | WARNING | console + file |
| Validation repair attempts | `extractor.extract:59` WARNING, invalid SQL at DEBUG | WARNING/DEBUG | DEBUG line goes to file only |
| Query executed (first 100 chars) + row count | `connection.execute_query:52` | DEBUG | **file only** — invisible at console |
| Connection established / closed | `connection.connect/close` | INFO | via stdlib logging |
| Unexpected errors + traceback | agent generic handler | ERROR | console + file |
| API unhandled errors + traceback | `routes.ask:33-34` | ERROR | root logging (basicConfig format) |

Log file mechanics (`utils/logger.py`): per-module named loggers; console=INFO, file=DEBUG; filename pinned to **process start date** (`app_YYYYMMDD.log`) — a long-running server keeps appending to yesterday's file.

## 2. Can we reconstruct one request end-to-end?

| Datum | Reconstructable? | How / blocker |
|---|---|---|
| Question text | **No** | never logged; only inside SQLite assistant/user messages if success path completed |
| session_id | No | not logged anywhere (UI prints to stdout only on client side) |
| Selected schema | Trivially yes (static FULL_SCHEMA) but not recorded per request | — |
| Full prompt sent to LLM | **No** | prompts never logged |
| Model name / temperature / tokens | No | not logged; constants in code |
| Raw LLM output before cleaning | No | not logged |
| Final generated SQL | **Yes** | "Generated SQL:" INFO line |
| Validation result per attempt | Partially | repair warnings exist w/o SQL content (DEBUG-only); final failure message only on error envelope |
| Repair attempts count | Inferable | by counting consecutive warning lines |
| Execution time (LLM/DB/total) | **No** | no timing anywhere |
| Row count returned | Yes | DB DEBUG file line ("(N rows)") when file logging active |
| Final answer text | Only via memory DB (success path) | not logged |
| Error + traceback | Yes | ERROR lines with traceback |

**Verdict**: a single successful request cannot be reliably reconstructed or correlated; interleaved concurrent requests are indistinguishable. The strongest available correlation is a timestamp window plus the Generated SQL line.

## 3. Log-quality issues found

1. **Volume pathology**: 2,510 of 2,584 lines in the main log are missing-translation WARNINGs (~97%) — signal drowned by presentation noise.
2. Duplicate handler risk: `connection.py:6` calls `logging.basicConfig(INFO)` at import; combined with per-module handlers, some records can appear twice/formats differ (root vs named logger formatting).
3. Mixed logging systems: `utils.logger` vs stdlib calls in `connection.py`, `api/main.py`, `api/routes.py` (root logger, different formatter).
4. Date-pinned filenames mislead for long-lived processes.
5. DEBUG-level operational detail (full query text) is written to files that are gitignored and never rotated/capped — unbounded growth on disk.
6. No request ids, no session ids, no latency metrics, no token usage capture (OpenAI response.usage discarded), no counters.

## 4. What exists beyond logs

- `data/conversations.db` doubles as an ad-hoc record of successful turns (question+answer text) — but without SQL, rows, model info, timings, and with second-granularity ambiguous ordering.
- `.schema_cache.json` records last schema hash only.

## 5. Missing observability (explicit list)

1. Request correlation id propagated UI→API→agent→LLM→DB.
2. Structured event per stage (prompt_built, llm_response, sql_validated, repair_attempted, exec_done, answer_generated) with durations.
3. Prompt/response payloads (or hashes) per call for debugging regressions.
4. Token accounting per request/day/provider.
5. Row-count + truncation flags for result handling.
6. Error taxonomy codes instead of raw strings.
7. Health metrics history (the /health endpoint is point-in-time only).
8. Any evaluation harness tying questions → expected outcomes (none exists; see evaluation/ baseline created this sprint).

No new tracing was implemented in Sprint 0 per scope; this list defines the target.
