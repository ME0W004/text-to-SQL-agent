# Component Contracts

| Interface | Input | Output | Errors | Invariants |
|---|---|---|---|---|
| QuestionAnalyzer.analyze | question, context | QuestionSpec | UnsupportedIntent, AmbiguousQuestion | no DB/SQL |
| SchemaCatalog.get_snapshot | version? | SchemaSnapshot | SchemaUnavailable, Drift | read-only immutable snapshot |
| SchemaRetriever.retrieve | spec, snapshot | SchemaContext | NoRelevantSchema | no invented fields |
| QueryPlanner.plan | spec, context | QueryPlan | PlanConflict, AmbiguousQuestion | explicit metric/unit/currency |
| SQLGenerator.generate | plan, context | SQLCandidate | LLMUnavailable, InvalidOutput | no approval/DB |
| SQLValidator.validate | candidate, snapshot | ValidationReport/AST | Syntax/SchemaReferenceError | pure; no generation/modification/execution |
| SecurityValidator.check | AST, policy | SecurityDecision | SecurityViolation | single read-only statement; fail closed |
| SemanticValidator.check | AST, plan, snapshot | SemanticReport | SemanticViolation | no silent intent changes |
| SQLRepairer.repair | candidate, diagnostics, plan | candidate | RepairExhausted | bounded; revalidate |
| QueryExecutor.execute | approved query, budget | QueryResult | Timeout, RowLimitExceeded, DB errors | only DB side effect; read-only |
| ResultAnalyzer.analyze | result, plan | ResultProfile | AnalysisError | deterministic provenance |
| PresentationPlanner.plan | spec, profile, policy | PresentationPlan | PolicyError | safe columns/formats |
| AnswerGenerator.generate | question, plan, profile, rows | AnswerDraft | GenerationError | grounded input only |
| AnswerValidator.validate | draft, profile | ValidatedAnswer | Ungrounded/FormatViolation | sanitize/reject |
| MemoryManager.append/get | typed turn/session | ack/context | MemoryUnavailable | ordered, transactional, retained |
| LLMRouter.generate | task, prompt, budget | text + provider/usage | ProviderUnavailable, BudgetExceeded | owns fallback/telemetry |

