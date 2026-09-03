"""Tests for the new state-applier methods introduced in Sprint 2 Prompt 2B.

These appliers are the contract surface that the Orchestrator uses to
write typed value objects into ``AgentState``.
"""

from __future__ import annotations

import pytest

from src.agents.contracts import (
    LocalePolicy,
    PresentationPlan,
    QueryPlan,
    QuestionSpec,
    ResultProfile,
    SchemaContext,
    ShapeDescriptor,
    ValidatedAnswer,
    TableRef,
)
from src.agents.state import (
    AgentState,
    PipelineStage,
    create_initial_state,
)


class TestQuestionAnalysisApplier:
    def test_with_question_analysis_records_spec_and_transitions(self):
        state = create_initial_state(question="q", session_id="s")
        spec = QuestionSpec(intent="lookup")
        new_state = state.with_question_analysis(spec)
        assert new_state.question_analysis is spec
        assert new_state.pipeline_stage == PipelineStage.QUESTION_ANALYZED

    def test_question_analysis_round_trip_via_to_dict(self):
        state = create_initial_state(question="q", session_id="s")
        spec = QuestionSpec(intent="aggregate")
        new_state = state.with_question_analysis(spec)
        data = new_state.to_dict(include_sensitive=True)
        assert data["pipeline_stage"] == PipelineStage.QUESTION_ANALYZED.value


class TestSchemaContextApplier:
    def test_with_schema_context_records_context_and_transitions(self):
        state = create_initial_state(question="q", session_id="s")
        state = state.with_question_analysis(QuestionSpec(intent="lookup"))
        ctx = SchemaContext(tables=(TableRef(name="T"),))
        new_state = state.with_schema_context(ctx)
        assert new_state.schema_context is ctx
        assert new_state.pipeline_stage == PipelineStage.SCHEMA_RESOLVED


class TestQueryPlanApplier:
    def test_with_query_plan_records_plan_and_transitions(self):
        state = create_initial_state(question="q", session_id="s")
        state = state.with_question_analysis(QuestionSpec(intent="lookup"))
        state = state.with_schema_context(SchemaContext())
        plan = QueryPlan()
        new_state = state.with_query_plan(plan)
        assert new_state.query_plan is plan
        assert new_state.pipeline_stage == PipelineStage.PLAN_CREATED


class TestResultProfileApplier:
    def test_with_result_profile_records_profile_and_transitions(self):
        state = create_initial_state(question="q", session_id="s")
        state = state.with_question_analysis(QuestionSpec(intent="lookup"))
        state = state.with_schema_context(SchemaContext())
        state = state.with_query_plan(QueryPlan())
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"a": 1}], 12.0)
        profile = ResultProfile(shape=ShapeDescriptor(rows=1, columns=2), row_count=1)
        new_state = state.with_result_profile(profile)
        assert new_state.result_profile is profile
        assert new_state.pipeline_stage == PipelineStage.RESULT_ANALYZED


class TestPresentationPlanApplier:
    def test_with_presentation_plan_does_not_transition_stage(self):
        state = create_initial_state(question="q", session_id="s")
        state = state.with_question_analysis(QuestionSpec(intent="lookup"))
        state = state.with_schema_context(SchemaContext())
        state = state.with_query_plan(QueryPlan())
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"a": 1}], 12.0)
        state = state.with_result_profile(
            ResultProfile(shape=ShapeDescriptor(rows=1, columns=2), row_count=1)
        )
        prior_stage = state.pipeline_stage
        plan = PresentationPlan(view_kind="table")
        new_state = state.with_presentation_plan(plan)
        # Presentation plan is a side-effect stage that doesn't transition.
        assert new_state.presentation_plan is plan
        assert new_state.pipeline_stage == prior_stage


class TestValidatedAnswerApplier:
    def test_with_validated_answer_records_and_transitions_to_answered(self):
        state = create_initial_state(question="q", session_id="s")
        state = state.with_question_analysis(QuestionSpec(intent="lookup"))
        state = state.with_schema_context(SchemaContext())
        state = state.with_query_plan(QueryPlan())
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"a": 1}], 12.0)
        state = state.with_result_profile(
            ResultProfile(shape=ShapeDescriptor(rows=1, columns=2), row_count=1)
        )
        answer = ValidatedAnswer(text="the answer")
        new_state = state.with_validated_answer(answer)
        assert new_state.validated_answer is answer
        assert new_state.pipeline_stage == PipelineStage.ANSWERED
        assert new_state.response.success is True
        assert new_state.response.answer == "the answer"


class TestInvariants:
    """Existing invariants from Sprint 1 / Prompt 1A/1B must continue
    to hold after the new appliers."""

    def test_state_validation_passed_keeps_invariants(self):
        state = create_initial_state(question="q", session_id="s")
        # Move through a successful chain.
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"a": 1}], 12.0)
        state = state.with_answer("ok")
        violations = state.validate_invariants()
        # No invariant violation expected on a happy path.
        assert violations == []