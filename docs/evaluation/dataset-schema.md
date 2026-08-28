# Canonical Evaluation Case Schema

The engine consumes `CanonicalCase` objects from `evaluation/models.py`. JSON adapters accept either a top-level list or an object containing `cases`.

| Field | Type | Required | Source behavior |
|---|---|---:|---|
| `id` | string | yes | Unique case identifier; duplicates are rejected. |
| `question` | string | yes | User question passed to the Agent. |
| `category` | string | yes | Dataset-defined category, preserved verbatim. |
| `difficulty` | `easy\|medium\|hard` | yes | Validated case-insensitively. |
| `expected_intent` | any/null | no | Structured intent in smoke data; absent in baseline is preserved as null. |
| `expected_tables` | array/null | no | Expected table names where supplied. |
| `expected_columns` | array/null | no | Expected column names where supplied. |
| `expected_behavior` | any/null | no | Semantic acceptance guidance; not automatically scored. |
| `gold_sql` | string/null | no | Optional reference SQL; neither existing dataset currently supplies it. |
| `notes` | string/null | no | Human context, traps or unresolved assumptions. |
| `source` | string/null | generated | Input file path added by the adapter for traceability. |

The adapter intentionally does not manufacture missing intent, tables, columns, gold SQL or expected row values. Baseline records with only the original six fields are valid canonical cases.
