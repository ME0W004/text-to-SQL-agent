# SQL Generation & Validation Pipeline Audit

> The complete SQL lifecycle as implemented. Empirical checks were run against the installed `sqlglot 30.17.0` during Sprint 0 (inspection only).

---

## 1. Lifecycle trace

```
User question
  → SystemPrompts.sql_generation_prompt(question, history)      [prompts.py:11-60]
  → LLMClient.generate → OpenAI chat completion                  [llm/client.py:34-43]
  → raw text output
  → SQLExtractor._clean_sql                                      [extractor.py:17-40]
      · strip ```sql / ``` fences
      · cut everything before first /(?:WITH|SELECT)\s+/i match
      · rstrip(';')
  → SQLValidator.validate                                        [validator.py:15-43]
      · non-empty, starts with SELECT/WITH
      · regex \b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|EXEC|EXECUTE)\b blocklist
      · sqlglot.parse_one(cleaned, dialect="tsql")
  → on SQLValidationError and attempts < max_retries(=2):
      → error_correction_prompt → LLM regenerate → clean → validate again
  → TextToSQLAgent.ask: DatabaseConnection.execute_query(sql)    [connection.py:43-56]
      · cursor.execute(sql); fetchall()
  → _filter_and_translate_rows → answer generation
```

Loop budget: initial call + at most **one** repair regeneration (`extractor.py:50-69`; `attempt` increments before check). If still invalid, the last `SQLValidationError` propagates to the agent's error envelope.

## 2. Stage-by-stage findings

### 2.1 Extraction/cleaning (`_clean_sql`)
- Markdown-fence stripping handles only leading ```` ```sql ````/```` ``` ```` and trailing ```` ``` ````.
- **Prefix-cut heuristic can change statement semantics**: for input like `INSERT INTO t SELECT …`, the regex keeps only the `SELECT…` tail — a rejected write silently becomes an executable read fragment. Verified by reading the regex; direction is "safe" but masks LLM misbehavior and defeats the blocklist's intent.
- Anything before the first SELECT/WITH (e.g., Persian preamble) is dropped — good — but text *after* the statement (e.g., explanation sentences) is **not** stripped and will flow into validation; sqlglot may parse it as part of the statement or fail with syntax errors that trigger pointless repairs.
- Trailing-semicolon strip is single; `;;` or internal semicolons survive.

### 2.2 Syntax validation (sqlglot)
- Dialect-correct (`tsql`). Catches genuine syntax errors and triggers repair.
- **Multi-statement gap (empirically confirmed)**:
  - `parse_one("SELECT 1; TRUNCATE TABLE Sale.Orders", dialect="tsql")` → parses OK (returns first statement; no error).
  - Same for `SELECT 1; DELETE FROM Sale.Orders` (caught only because DELETE is block-listed), and `SELECT …; EXEC xp_cmdshell 'dir'` parses fine (EXEC blocked only by the text regex).
  - Consequence: any trailing statement using a **non-blocklisted** keyword passes validation and reaches `cursor.execute`, which will execute both statements sequentially. Examples that pass today: `TRUNCATE TABLE`, `GRANT`, `REVOKE`, `DENY`, `MERGE` (contains no blocked word? MERGE uses WHEN MATCHED THEN UPDATE/INSERT/DELETE — those words appear → blocked by regex; but plain `TRUNCATE`/`BACKUP DATABASE`/`RESTORE`/`SHUTDOWN`/`KILL`/`OPENROWSET` reads are not), `SELECT … INTO new_table`.
- No AST-level check of statement type/count; prefix string check is trivially satisfied.

### 2.3 Security validation
- Blocklist is textual, so it also false-positives on identifiers/comments containing keywords (e.g., a column named `created_at` is safe due to `\b`, but a comment containing “update” rejects a benign query).
- Not blocked: TRUNCATE, MERGE (as top keyword it would be caught only if UPDATE/INSERT/DELETE appear in its USING/WHEN clauses — usually yes, but `MERGE … WHEN NOT MATCHED THEN INSERT` always contains INSERT; edge cases remain), GRANT/DENY/REVOKE, BACKUP/RESTORE, SHUTDOWN, KILL, OPENROWSET/OPENDATASOURCE/ad-hoc linked servers, `xp_*`/`sp_*` procedure calls via `EXECUTE` alias forms already covered only when the word EXECUTE appears.
- No allowlist of schemas/tables/columns against the actual DB; the model may query any object the login can see.
- No enforcement at the connection level (login permissions unknown/unmanaged; app uses one privileged-style login for everything).
- Params support exists in `execute_query` but nothing uses it — all values arrive as literals interpolated by the LLM (string/date literals; injection surface limited to whatever the model emits, but user-influenced strings go through the model, not format-strings, so classic injection is indirect).

### 2.4 Schema validation
- **Absent.** Nothing verifies referenced tables/columns exist. Typos/hallucinated columns become runtime `pymssql` errors (e.g., invalid column name) that skip the repair loop entirely (execution stage has no repair path).

### 2.5 Semantic validation
- **Absent.** No unit checks (KG vs TN vs LT vs M3), no currency awareness (RLS vs USD/EUR/AED/RUB orders coexist; `SUM(quantity*unitPrice)` across currencies is currently accepted), no cancelled-order exclusion (`status='ابطال'` rows count toward revenue), no date sanity (few-shot teaches year '1403' which cannot exist), NULL-handling unchecked (`quantity`/`unitPrice` nullable).

### 2.6 Aggregation correctness
- Prompt rule 13 pushes aggregates into HAVING (good); log evidence shows violations still occur and die at execution: `An aggregate may not appear in the WHERE clause…` found in `logs/app_20260821.log`. Such errors are **not** repaired — they surface to the user.
- COUNT vs SUM disambiguation exists only as prompt prose; no verification that returned columns match question intent.
- Duplicate Shipments Ids (795 duplicate groups in data) make naive joins/counts on Shipments unreliable; nothing guards this.

### 2.7 Join correctness
- Join keys provided in prompt (rule 12) and few-shots; no validator confirmation that generated joins reference existing keys (they do exist logically even though DB declares no FK constraints — verified: `sys.foreign_keys` empty).
- LEFT JOIN guidance exists (rule 15) but correctness (which side to preserve) is left to the model.

### 2.8 Query complexity / cost
- **Absent.** No LIMIT/TOP enforcement for unaggregated dumps (`SELECT * FROM Sale.Orders` was actually generated per logs and would fetch 16,948×16 wide rows into memory and render them), no cost estimation, no timeout beyond connect-time `timeout=5` seconds which will kill legitimate heavy aggregations instead.

### 2.9 Multi-statement detection
- Only accidental, via the keyword regex. See 2.2 — insufficient (empirical bypass demonstrated with TRUNCATE).

### 2.10 Dangerous-SQL detection overall
- Prefix + 8 keywords + parse = the entire defense. Documented bypasses above. Also note `_clean_sql` may *manufacture* a passing SELECT out of a failing write statement (2.1).

### 2.11 Repair loop
- Triggered exclusively by `SQLValidationError`; max 1 repair; repair prompt lacks schema/rules/few-shots (`prompt-audit.md` §Prompt 3); no diff/log of what changed between attempts beyond two log lines; success/failure per attempt not surfaced in the response payload.

## 3. Execution-stage gaps (post-validation)

| Gap | Detail |
|---|---|
| No row cap | `fetchall()` unbounded; UI renders everything; answer prompt sees only 5 |
| Fixed 5 s query timeout | `pymssql.connect(timeout=5)` (`connection.py:28`) — heavy scans/aggregations over 37k–17k row heaps (no indexes at all) risk timeout failures |
| Shared cursor | One cursor reused; concurrent API requests interleave on it unsafely |
| Error typing | Raw `pymssql` exceptions propagate; agent maps them via dead `QueryExecutionError` branch → falls to generic handler |

## 4. Observed real-world pipeline outcomes (log evidence)

1. Valid analytical queries succeed end-to-end (multiple `Generated SQL:` INFO lines with TOP/GROUP BY patterns).
2. Validation rejection worked for an explicit DELETE request (`report.md` test 2).
3. Aggregate-in-WHERE produced an execution error that reached the user unrepaired (`logs/app_20260821.log`, pymssql exception 147).
4. Unbounded `SELECT *`-style queries were generated and executed (log line `Generated SQL: SELECT *`).
5. Missing-translation warnings (2,510/day) show downstream presentation fragility, not SQL issues, but confirm alias-heavy outputs.

## 5. Weakness summary table

| Area | Rating | Key evidence |
|---|---|---|
| SQL extraction from LLM output | Weak | fence-only stripping; post-statement text unhandled (`extractor.py:17-40`) |
| Cleaning | Weak–Risky | prefix-cut can mutate statement type |
| Parsing | Adequate | sqlglot tsql parse works |
| Syntax validation | Adequate | catches syntax; feeds repair loop |
| Security validation | **Critical gap** | TRUNCATE/multi-statement bypass proven empirically |
| Schema validation | Missing | none |
| Semantic validation | Missing | currency/unit/status blindness |
| Aggregation correctness | Weak | prompt-only; live failure observed |
| Join correctness | Weak | prompt-only guidance |
| Complexity/cost | Missing | unbounded SELECT * executed |
| Multi-statement detection | **Insufficient** | parse_one ignores trailing statements |
| Repair loop | Partial | validation-only, context-starved prompt, single retry |
