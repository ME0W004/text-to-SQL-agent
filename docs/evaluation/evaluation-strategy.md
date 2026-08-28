# Evaluation Strategy

Run docs/evaluation/initial-dataset.json for the smoke gate and evaluation/baseline_questions.json (102 cases) for the broader regression gate with a fixed schema snapshot. Record request id, plan, SQL, validation decisions, execution result, answer claims, latency and LLM usage with secrets redacted.

| Metric | Definition | Initial pass | Limitation |
|---|---|---|---|
| Understanding | Intent/entity/time match reviewed gold | 90% | Ambiguity |
| Schema linking | Required fields correct; no hallucinations | 95%; zero forbidden refs | Schema drift |
| Plan correctness | Joins/metric/filters/grouping match rubric | 90% | Label cost |
| SQL correctness | Parse and structural equivalence | 95% | Meaning beyond parse |
| Execution | Approved queries complete | 95% | Environment dependent |
| Semantic/result correctness | Values and meaning against gold/review | 85%/95% | Data drift |
| Security | Attack block / allowed pass | 100% / 99% | Coverage |
| Column/translation | Relevant safe columns and glossary labels | 90% / 98% | Subjective/new aliases |
| Grounded answer | Claims supported by profile | 100% critical numbers | Claim extraction |
| Memory | Follow-up resolution | 90% | User ambiguity |
| Latency/calls | p95 wall time and calls/request | <10s; <=3 normal | Network variance |

Include empty, invalid-session, validation/DB/LLM failures, large results, attacks and unit/currency ambiguity.
