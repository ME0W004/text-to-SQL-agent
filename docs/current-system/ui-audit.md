# UI Audit (Streamlit)

> Single file: `src/web/app.py` (336 lines). Runs as a pure API client — no direct DB/LLM access.

---

## 1. Layout

- Wide layout, page title "Text-to-SQL Agent" (`st.set_page_config`, lines 12–16).
- Main area: title + subtitle (180–181), chat history (204–228), chat input at bottom (232).
- Sidebar: "Session Management" with a single **Start New Session** button and an Information block (message count, truncated session id) (184–201).

## 2. Chat flow

1. User submits via `st.chat_input` (232).
2. User message appended to `st.session_state.messages` and rendered immediately (234–243).
3. `send_question()` POSTs `{question, session_id}` to `{API_URL}/ask`, `timeout=60` (27–51); spinner wraps the wait (247).
4. On success: stores/updates `session_id` from response (259–275), renders answer markdown **with `unsafe_allow_html=True`** (278–281), renders `pd.DataFrame(rows)` via `st.dataframe` (284–291), SQL expander (294–300), appends assistant message `{role, content, rows, sql}` to local history (303–310).
5. On failure: extracts `error`, `st.error`, shows offending SQL if present, appends `{role:'assistant', content:'Error: …'}` to history (312–336).

## 3. State management & session handling

- Only two keys in `st.session_state`: `messages`, `session_id` (20–24). Streamlit session ≠ server session; server sessions live forever (see memory-audit M3).
- "Start New Session" resets both fields locally and `st.rerun()`s — server-side session orphaned.
- History replay renders every stored assistant message with its rows + SQL expander after each rerun (Streamlit full-repaint model).

## 4. API communication

- Base URL from env `API_URL` default `http://localhost:8000/api/v1` (18).
- Error branches: HTTP != 200 → generic status error; ConnectionError → dedicated message; all other exceptions → generic (41–51).
- Leftover debug `print()` statements for session tracking (31, 264–275).

## 5. Rendering details

| Element | Implementation | Notes |
|---|---|---|
| Answer | `st.markdown(content, unsafe_allow_html=True)` | model-controlled HTML → XSS/prompt-injection surface |
| Rows | `pandas.DataFrame` + `st.dataframe(width='stretch')` | duplicates the table already inside the answer Markdown; unbounded rows |
| SQL | expander + `st.code(language='sql')` | also displayed on failures |
| Errors | `st.error(f"Error: {msg}")`; failure text persisted into chat history | raw provider/DB messages leak to end users |
| Empty result | rendered as normal answer text ("No data found…") | English-only |

## 6. RTL behavior & theme

- One global CSS block injected every run (58–177) forcing RTL on app, sidebar, headings, chat messages, chat input, expanders, alerts, captions, data frames.
- Code/pre/SQL forced LTR via `!important` (156–167) — sensible split.
- `[data-testid="stDataFrame"] * { direction: rtl }` can visually garble mixed Latin/Persian cell content (e.g., part numbers '3840 UA').
- No theme configuration beyond this CSS; relies on default Streamlit theme.

## 7. Robustness gaps observed

1. No handling of malformed success payloads (assumes `result["answer"]` exists when `success==true`).
2. 60 s client timeout vs possible server-side long operations (startup column-map regeneration + LLM calls) → user-facing timeouts on first request after schema change.
3. No retry/backoff; a transient proxy failure surfaces as raw error text.
4. Session continuity depends on server echo of `session_id`; if absent it silently keeps old one (269–275) which is correct only because server replaces unknown ids silently — coupling of two lenient behaviors.
5. Persian users get an English chrome (title/subtitle/sidebar labels) despite RTL support — cosmetic inconsistency with the product language.
