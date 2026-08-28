# Migration Plan

1. Safety/evaluation: add harness, trace IDs, typed errors, AST single-statement/read-only policy and execution caps behind flags. Roll back by disabling flags.
2. Schema: introduce live versioned SchemaCatalog and label metadata while retaining static adapter.
3. Planning: add QuestionSpec/QueryPlan facade with legacy extractor fallback.
4. Validation/repair: split syntax, security, schema, semantic and complexity diagnostics; bounded repair with replay tests.
5. Execution/results: request-scoped DB sessions, limits, ResultProfile and PresentationPlan; retain legacy adapter for rollback.
6. LLM/memory: route providers through LLMRouter and add layered memory with provenance/retention.
7. Cutover: answer validation and UI sanitization, then deprecate legacy path after parity.

Each phase preserves /api/v1/ask, is independently testable and requires baseline replay before widening traffic. Sprint 1 implementation is not started.

