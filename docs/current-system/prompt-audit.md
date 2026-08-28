# Prompt Audit

> Every prompt in the codebase. Audit only — no rewrites. Line numbers from commit `e6b53d6`.

---

## Prompt 1 — SQL generation

| Field | Value |
|---|---|
| Location | `src/core/prompts.py:11-60` (`SystemPrompts.sql_generation_prompt`) |
| Purpose | Convert a Persian natural-language question into one T-SQL SELECT/CTE statement. |
| Caller | `SQLExtractor.extract` (`sql_extractor/extractor.py:46`) |
| Inputs | `question: str`, optional `history: list[{role, content}]` (last 5 messages, already truncated by caller) |
| Output format expected | "Return ONLY the SQL query" (rule 8); trailing cue `**SQL query:**` |
| Model / temp / tokens | gpt-4o-mini via proxy · 0.1 · 500 |
| Schema/context supplied | `FULL_SCHEMA` (`database/models.py:81-125`) — Orders + OrderItems columns with English semantic glosses; **Sale.Shipments columns are absent** (only a relationship note). Plus 16 numbered rules; plus 5 few-shots. |
| Few-shot usage | 5 static examples appended verbatim (`few_shots.py`): top-10 buyers by revenue 1403 (**year not present in data — max is 1399**), sales per product group 1399, avg order value 1399, order counts per product group, monthly totals 1399. |
| Memory/history usage | Raw last-5 messages appended after few-shots with meta-instructions to use them only for reference resolution (lines 43-46, 53-57). Assistant messages included **with full Markdown answers**, inflating tokens and duplicating prior result data. |

**Strengths**
- Explicit dialect rules that match the real schema (TOP vs LIMIT, GETDATE, `jalaliYearNo`, `operationMonth`, join keys, HAVING rule) — these encode genuinely hard-won domain knowledge.
- Count-vs-sum disambiguation rule (rule 16) targets a real Persian ambiguity («تعداد» vs «مجموع مبلغ»).
- History-scoping instruction attempts to prevent history-driven query drift.

**Weaknesses / failure modes**
1. **Shipments blind spot**: rules mention Shipments joins (rule 12) but the schema block never shows its columns → any question about deliveries/weights/drivers forces the model to guess column names. Hallucination-prone.
2. **Stale few-shot year**: `'1403'` example teaches a pattern guaranteed to return empty rows on this DB (years: 1394–1399).
3. **No output contract enforcement**: "ONLY SQL" is prose only; no stop sequences, no structured outputs; cleaning must compensate.
4. **Rule/few-shot redundancy & drift risk**: revenue formula and join keys appear in rules AND few-shots AND FULL_SCHEMA footer; three copies can drift.
5. **Ambiguity left open**: no guidance for cancelled orders (`status='ابطال'`), currency mixing (RLS vs USD/EUR/AED/RUB orders exist), unit variance (KG/TN/LT/M3), duplicate Shipments Ids — so «کل فروش» silently mixes currencies today.
6. **History block placement**: appended after examples, unannotated roles beyond `role:` prefix; long assistant Markdown answers waste budget under the 500-token cap.
7. **Missing negative constraints**: no ban on `SELECT *`, no row-limit guidance for raw dumps, no NULL-handling hints (quantity/unitPrice nullable), no instruction about `operationMonth` being an INT (YYYYMM) vs `jalaliYearNo` varchar.

---

## Prompt 2 — Natural-language answer generation

| Field | Value |
|---|---|
| Location | `src/core/prompts.py:63-88` (`SystemPrompts.natural_response_prompt`) |
| Purpose | Explain query results in Persian as rigid Markdown. |
| Caller | `TextToSQLAgent.ask` (`text_to_sql_agent.py:88-93`) |
| Inputs | question; pre-translated rows (Persian headers, `*Id` dropped); row_count. Only first ≤5 rows embedded via `str(row)` (Python dict repr). |
| Output format demanded | Pure Markdown starting with `# Summary`, then `## Key insights`, `## Data table`, `## Conclusion`; no code fences; headings exemplified inconsistently (`# خلاصه` vs `# Summary`) |
| Model / temp / tokens | same model · 0.1 · 500 |

**Strengths**
- Fixed section structure gives the UI predictable rendering; Persian requirement explicit.

**Weaknesses / failure modes**
1. **Heading-language contradiction**: template says sections `# Summary` etc. but example shows `# خلاصه`; real outputs mix both (observed in `data/conversations.db`: `"# خلاصه ... ## Key insights"`).
2. **Data truncation**: with >5 rows the "Data table" section cannot contain the actual table; the model is invited to fabricate or generalize — hallucination surface.
3. **Dict-repr serialization**: `str(row)` produces Python reprs with single quotes; large floats appear in raw precision; Persian keys inside reprs hurt readability and token efficiency.
4. **No numeric grounding instructions**: model free to invent percentages/totals ("include totals… where relevant") without recomputation ability → arithmetic hallucination risk.
5. **max_tokens=500**: long tables/insights get cut mid-Markdown; UI renders broken tables.
6. **Empty-result path bypasses this prompt entirely** — fixed English string instead (`text_to_sql_agent.py:85-86`), inconsistent language/format.
7. Rows arrive already filtered/translated: ID-like columns removed *before* answering, so the answer step can't reference entity identifiers even when useful.

---

## Prompt 3 — Error-correction (repair)

| Field | Value |
|---|---|
| Location | `src/core/prompts.py:91-108` (`SystemPrompts.error_correction_prompt`) |
| Purpose | Regenerate SQL after validation rejection. |
| Caller | `SQLExtractor.extract` retry branch (`extractor.py:63-68`) |
| Inputs | validator error message, invalid SQL, original question |
| Output format | "Only return the SQL query"; ends `SQL:` |
| Model / temp / tokens | same · 0.1 · 500 |

**Strengths**: minimal, focused; includes the concrete validator error.

**Weaknesses / failure modes**
1. **Context starvation**: says "Follow the schema and rules strictly" but supplies **neither schema nor rules nor few-shots** → repair quality depends on the model's memory of nothing; high chance of repeated violation.
2. Validator messages like `"Query contains forbidden keyword: INSERT"` push the model to hide keywords rather than fix semantics (e.g., rewriting DELETE-intent into nonsense SELECTs).
3. No mention of T-SQL dialect, TOP syntax, jalali columns — the very things most likely to have caused rejection.
4. Only triggered for validation errors; **execution errors never enter this loop** (log evidence: aggregate-in-WHERE error surfaced raw to user).

---

## Prompt 4 — Column-map translation (generator script)

| Field | Value |
|---|---|
| Location | `scripts/generate_column_map.py:47-60` (`translate_chunk`) |
| Purpose | Translate ≤15 column names to Persian as strict JSON. |
| Caller | `translate_columns_with_llm` chunk loop; also invoked indirectly at startup via subprocess when schema hash changes |
| Inputs | plain column-name lines + counts |
| Output format | JSON object; regex-extracted `\{.*\}` |
| Model / temp / tokens | same · 0.1 · 500 |
| Context supplied | Column names only — **no table names, no data types, no sample values** |

**Strengths**: chunking keeps JSON small; explicit item-count demand; identity fallback on failure.

**Weaknesses / failure modes**
1. **Proven cause of wrong translations**: without context, `mesc` was translated «مقدار» ("amount"); true meaning is material code (`database/models.py`; DB samples are zero-padded codes like '0000007194'). The wrong entry persists in the active map.
2. Non-deterministic regeneration: each run may produce different labels (active map vs stray `scripts/src/core/column_mapper.py` differ, e.g., `jalaliYearNo`).
3. Regex `\{.*\}` greedy across a chatty response can capture garbage; failure mode is silent identity mapping.
4. Output of this prompt becomes **executable Python source** written into `column_mapper.py` with no escaping/validation — a Persian label containing a quote would corrupt the module.

---

## Cross-cutting observations

- **No system-role usage anywhere**; all prompts ride in a single user message.
- **One temperature/token budget for all tasks** (0.1 / 500) — see llm-audit.
- **Prompt knowledge duplicated in 3 places** (rules text, FULL_SCHEMA footer, few-shot bodies) with no single source of truth.
- **No versioning/tests for prompts**; changes are unverifiable except by eyeballing outputs.
- **Hallucination pressure points**: missing Shipments schema, >5-row truncation, empty-year few-shot, repair-without-schema, translation-without-context.
