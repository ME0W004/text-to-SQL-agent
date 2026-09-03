# ADR-014: Validator Separation (SQL / Security / Semantic / Complexity)

Status: Accepted.

Decision: Four distinct validator interfaces, each with a single
responsibility:

- `SQLValidator` — syntax and AST parse for the declared dialect.
- `SecurityValidator` — fail-closed AST policy (one read-only SELECT).
- `SemanticValidator` — plan-vs-SQL semantics (joins, filters, units,
  currencies, business status).
- `ComplexityValidator` — cost budget (joins, depth, function count).

Each validator returns a typed report object. Validators do not raise
typed errors by default; the Orchestrator decides whether to raise based
on policy. Validators are pure, deterministic, and free of LLM, DB and
network dependencies.

`SecurityValidator` is the authoritative security boundary
(ADR-004). It cannot be bypassed or removed. `SQLValidator`,
`SemanticValidator` and `ComplexityValidator` are correctness checks and
do not enforce security.

Rationale: separating these responsibilities prevents a single
validator from growing into a God Object that knows about syntax,
security, semantics and cost. Each validator is independently testable
and replaceable.

Consequence: the current `SQLValidator` (in
`src/core/sql_extractor/validator.py`) is a thin wrapper that delegates
to `SQLSecurityValidator`. The wrapper is preserved for backward
compatibility with `SQLExtractor.extract()` and will be removed when
`SQLExtractor` is replaced by `SQLGenerator` + `SQLRepairer` in Prompt 2B
or Sprint 4.