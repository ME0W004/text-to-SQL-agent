# ADR-001: Separate Question Understanding from SQL Generation

Status: Accepted for target architecture. Context: current SQL prompt combines interpretation and rendering. Decision: QuestionAnalyzer emits a typed QuestionSpec; SQLGenerator consumes a QueryPlan. Consequence: ambiguity and regressions become testable; an extra boundary is maintained.

