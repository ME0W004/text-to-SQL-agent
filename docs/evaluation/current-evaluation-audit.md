# Current Evaluation Audit

## What existed before Sprint 1

- `evaluation/baseline_questions.json`: a 102-case object with `meta` and `cases`; every case has `id`, `category`, `question`, `difficulty`, `notes` and `expected_behavior`.
- `docs/evaluation/initial-dataset.json`: a 25-case list; cases add `expected_intent`, `expected_tables` and `expected_columns`, with no `gold_sql` values.
- `evaluation/README.md`, `expected-behavior.md` and Sprint 0 evaluation documents: descriptive guidance, but no executable runner or persisted run schema.
- `tests/`: only empty package initializers before this Sprint; no pytest evaluation tests or integration harness.

## Runtime constraints

The production Agent can be imported and called without Streamlit, but construction requires `DB_*` configuration, a reachable MSSQL server and (for normal generation) an OpenAI-compatible provider. Therefore end-to-end evaluation is environment-dependent. Dataset parsing, normalization, metric calculation, failure classification, serialization, comparison and CLI offline mode are independent of those services.

## Gaps addressed by Sprint 1

The new `evaluation/` modules provide a canonical adapter, lazy Agent adapter, bounded per-case runner, conservative metrics, typed failure categories, JSON/Markdown persistence and run comparison. The CLI supports dataset filters and `--offline` validation.

## Deliberately not measured automatically

SQL semantic correctness, schema linking, query-plan correctness, answer groundedness and translation quality are not inferred from strings. They remain `not automatically measurable` until gold results, schema snapshots or reviewer labels are supplied. `gold_sql` is preserved as optional diagnostic data only.
