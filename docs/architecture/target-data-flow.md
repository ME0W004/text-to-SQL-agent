# Target Data Flow

User question -> Session/context -> QuestionAnalyzer -> SchemaRetriever -> QueryPlanner -> SQLGenerator -> SQLParser -> SecurityValidator -> SQL/schema validation -> SemanticValidator -> ComplexityValidator -> bounded Repairer loop (back to parser) -> ExecutionGuard/QueryExecutor -> Database -> ResultAnalyzer -> PresentationPlanner -> AnswerGenerator -> AnswerValidator -> Memory update -> Response.

Every repaired candidate is revalidated. Empty, partial and error states remain distinct. Database access occurs only after read-only, single-statement, cost and result limits pass.

