# Result Presentation & Column Translation Audit

> Covers: row filtering, ID removal, column selection/translation, schema-hash driven map updates, formatting/answer generation, and how results reach the UI. Code: `text_to_sql_agent._filter_and_translate_rows`, `column_mapper.py`, `column_mapper_updater.py`, `schema_hash.py`, `prompts.natural_response_prompt`, `web/app.py` rendering.

---

## 1. Pipeline of a result row today

```
pymssql dict row (English keys)
  → drop keys where key == "Id" or endswith("Id")     [agent:50-51]
  → rename remaining keys via static COLUMN_MAP        [agent:53-58]
      · miss ⇒ keep original name + one WARNING per unseen column
  → translated rows list[dict]
  → answer prompt receives rows[:5] as str(row) + full row_count
  → UI renders: answer Markdown (contains its own "Data table")
       + st.dataframe(translated rows)
       + SQL expander
```

## 2. Column translation mechanics

| Aspect | Current state | Reference |
|---|---|---|
| Map source | Auto-generated Python dict literal committed to source; 41 entries | `src/core/column_mapper.py` |
| Generation trigger | Manual script run OR startup hash mismatch | `generate_column_map.py`, `column_mapper_updater.py` |
| Hash basis | MD5 over INFORMATION_SCHEMA columns (name/type/nullable) of the 3 tables | `schema_hash.py:8-33` |
| Cache | `.schema_cache.json` at repo root | `column_mapper_updater.py:13` |
| Regeneration path bug | Updater runs generator with `cwd=scripts/`; generator writes CWD-relative `src/core/column_mapper.py` ⇒ output lands in `scripts/src/core/column_mapper.py`. **The imported map is never updated automatically.** Stray tracked file proves it happened. | updater lines 40–47; generator line 108 |
| Translation quality issues | `mesc` → «مقدار» ("amount") — wrong (material code). `entityDate` → «تاریخ موجودیت» (literal "entity date", not "creation date"). Stale alias entry `delivered_quantity` present. Two historical generations disagree (`jalaliYearNo`: «سال شمسی» vs «شماره سال جلالی»). | compare both files |
| Coverage gap by design | Query aliases/aggregates (`total_sales`, `avg_order_value`, `order_month`, `delivered_quantity`…) can never be in a column-name map generated from INFORMATION_SCHEMA → English leaks into Persian tables. Log evidence: **2,510 “Missing translation for column” warnings on Aug 21 alone**; the map even contains `delivered_quantity` precisely because someone patched a past leak into the generator output. | logs; map contents |

**Answer to the sprint question**: the system does NOT understand semantic importance of columns. It applies two static rules only:
1. name-suffix rule (`*Id` removed),
2. dictionary lookup.
There is no notion of measure vs dimension, no relevance ranking, no per-question column selection. The *answer prompt* asks the model to pick "most relevant columns" for its Markdown table (`prompts.py:82`) — i.e., semantic selection is delegated to an LLM with truncated data.

## 3. ID removal logic

- Removes any key ending in `Id` plus exact `Id` — including meaningful identifiers like `buyerId`, `destinationId`, `orderNo` is kept but `orderId` dropped. Since buyerId etc. are dangling references (no lookup tables), dropping them is defensible *today*, but the rule is blind: a future legit column like `invoiceId` would vanish too.
- Removal happens **before** answer generation: the answering model cannot reference entity ids even when the user's question is about a specific id.

## 4. Empty-result handling

- `rows == []` ⇒ fixed English `"No data found for your query."` (`agent:85-86`). No LLM call, no Persian, no diagnosis (was it a wrong year? wrong filter?), yet it's stored in memory as the assistant turn and later fed back as history.

## 5. Formatting & rendering

| Surface | Behavior | Issues |
|---|---|---|
| Answer Markdown | LLM free-form under rigid 4-section contract; headings language inconsistent in practice («خلاصه» vs "Key insights" mixed) | truncation risk @500 tokens; hallucinated numbers possible when >5 rows truncated |
| Data table #1 | inside answer Markdown (model-written) | may omit/fabricate values |
| Data table #2 | `pd.DataFrame(rows)` + `st.dataframe` — full result set | duplicate presentation; unbounded width/rows |
| SQL | expander with st.code | fine; also shown on errors |
| Errors | `st.error` + offending SQL appended to chat history | raw pymssql/HTTP text shown to users |
| RTL | global CSS injection; code blocks forced LTR | reasonable approach; DataFrame cells forced RTL may garble mixed Latin content |
| HTML safety | assistant markdown rendered with `unsafe_allow_html=True`; data values originate from DB and could contain markup | XSS/prompt-injection surface |

## 6. Row processing details worth recording

- Filtering iterates every key of every row per request — O(rows×cols), fine at this scale.
- The translated rows are what get persisted? No — memory stores only question+answer text, not rows (good for DB size, bad for audit reconstruction).
- `row_count` returned to UI equals len(translated rows); identical to pre-filter count (filter never drops rows).
- No pagination anywhere: a 16,948-row SELECT * flows fully to Streamlit.

## 7. Findings summary

1. Translation is static-name-based only; semantic column importance absent (explicitly delegated to the answer LLM with ≤5 rows visible).
2. Auto-update mechanism is broken in effect (CWD bug) — active map drifts from DB silently; only manual regeneration helps.
3. Aliases will always leak English headers; current workaround is ad-hoc patching of the generated file.
4. Known-wrong translation shipped (`mesc`) and non-deterministic regeneration makes fixes unstable.
5. Double table rendering + truncation-based answer table create inconsistent numbers between sections.
6. Empty-result UX is an English dead-end contradicting the Persian product language.
