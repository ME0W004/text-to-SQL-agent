# Prompt Audit

sql_generation_prompt combines question interpretation, schema linking, SQL generation, formatting rules and security guidance. It includes static FULL_SCHEMA (Orders and OrderItems descriptions but no Shipments columns), 16 rules, five fixed few-shots and up to five raw history messages. error_correction_prompt contains question, error and invalid SQL but omits the schema/rules/few-shots it references. natural_response_prompt asks for Persian four-section Markdown while exposing only row_count and first five rows as Python dict text. Column translation uses chunked LLM JSON extraction. Risks: token growth, stale examples, mixed responsibilities, alias translation gaps, partial-data hallucination and unchecked formatting. Prompts were not rewritten.

