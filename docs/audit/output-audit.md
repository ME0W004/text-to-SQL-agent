# Output Audit

Database rows are list-of-dicts. Agent removes keys named Id or ending in Id, then translates with static COLUMN_MAP; aliases/computed columns often remain English and emit warnings. No relevance-based projection exists. The answer LLM sees at most five rows while Streamlit renders all rows again in a DataFrame, causing duplicate presentation and unbounded UI work. Empty result text is English. Numeric, date, currency, unit and Persian formatting are not deterministic. unsafe_allow_html=True renders model-controlled HTML.

