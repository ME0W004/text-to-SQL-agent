# ADR-013: Single Writer of AgentState

Status: Accepted.

Decision: Only the pipeline Orchestrator writes `AgentState`. Components
return typed results; the Orchestrator applies those results to the
state via the existing convenience methods (`with_generated_sql`,
`with_validation_passed`, etc.) and the new state-applier methods
(`with_question_analysis`, `with_schema_context`, `with_query_plan`,
`with_result_profile`, `with_presentation_plan`, `with_validated_answer`).

Components MUST NOT import `AgentState` or any of its sub-models. They
receive typed value objects (`QuestionSpec`, `QueryPlan`, `SchemaContext`,
etc.) as parameters.

Rationale: this prevents components from becoming hidden God Objects
that share mutable state. The current `TextToSQLAgent.ask()` follows this
rule implicitly (verified in Sprint 2 Prompt 1C). The rule is made
explicit now so future components cannot silently violate it.

Consequence: future architecture tests will statically assert that no
component module imports `src.agents.state` or its sub-models.