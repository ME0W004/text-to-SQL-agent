# Target Architecture

Thin Agent/Application Service coordinates: QuestionAnalyzer (intent, entities, ambiguity), SchemaCatalog (live versioned metadata and labels), SchemaRetriever, QueryPlanner, SQLGenerator, SQLParser, SQLValidator, SecurityValidator, SemanticValidator, ComplexityValidator, bounded SQLRepairer, read-only QueryExecutor, ResultAnalyzer, PresentationPlanner, AnswerGenerator, AnswerValidator, layered MemoryManager, LLMRouter, and Evaluation/Observability.

Analyzer and planner may use an LLM for interpretation, but deterministic invariants decide approval. SQLValidator validates AST/dialect only and must not generate, modify or execute. SecurityValidator fails closed for one read-only statement. SemanticValidator checks joins, metrics, units, currencies and filters. QueryExecutor is the only DB boundary and enforces isolation, timeout and row/column caps. ResultAnalyzer and PresentationPlanner are deterministic; AnswerGenerator verbalizes only supplied facts; AnswerValidator checks claims and sanitizes output. Router owns provider, model, retry and budget policy.

