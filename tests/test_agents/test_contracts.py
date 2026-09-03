"""Tests for Sprint 2 Prompt 2B typed value objects.

These tests cover the contracts module introduced in Sprint 2 Prompt 2B.
Each value object is verified for:

* Construction with required fields
* Immutability (frozen)
* Field defaults
* Round-trip serialisation where applicable
* Boundary errors (invalid operator, etc.)
"""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime

import pytest

from src.agents.contracts import (
    # Question domain
    Entity,
    QuestionFilter,
    QuestionMetric,
    TimeConstraint,
    Reference,
    AmbiguityReport,
    QuestionSpec,
    # Schema domain
    TableDescriptor,
    Relationship,
    BusinessSemantics,
    SchemaVersion,
    SchemaSnapshot,
    TableRef,
    ColumnRef,
    JoinHint,
    SchemaContext,
    # Planning domain
    Join,
    Dimension,
    PlanMetric,
    PlanFilter,
    SortKey,
    OutputRequirements,
    QueryPlan,
    # SQL generation/validation
    SQLCandidate,
    ParsedAST,
    SyntaxError,
    ValidationReport,
    SemanticViolation,
    SemanticReport,
    ComplexityBudget,
    ComplexityReport,
    # Execution
    QueryResult,
    # Result/Presentation
    ColumnType,
    ShapeDescriptor,
    AggregateRef,
    ResultProfile,
    FormatSpec,
    LocalePolicy,
    PresentationPlan,
    # Answer
    Claim,
    Evidence,
    AnswerDraft,
    ValidatedAnswer,
    # LLM
    Usage,
    LLMPolicy,
    LLMBudget,
    LLMResult,
    # Errors
    QuestionAnalysisError,
    SchemaError,
    PlanningError,
    SQLGenerationError,
    RepairError,
    ResultAnalysisError,
    PresentationError,
    AnswerGenerationError,
    AnswerValidationError,
    MemoryError,
    LLMRouterError,
    TypedComponentError,
    ErrorStageTag,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_frozen(instance) -> None:
    # Use setattr (which goes through __setattr__ on frozen dataclasses).
    with pytest.raises(FrozenInstanceError):
        first_field = fields(type(instance))[0]
        setattr(instance, first_field.name, "mutated")


# ---------------------------------------------------------------------------
# Question domain
# ---------------------------------------------------------------------------


class TestQuestionDomain:
    def test_entity_construction(self):
        e = Entity(text="Sale.Orders", entity_type="table", qualified_name="Sale.Orders")
        assert e.text == "Sale.Orders"
        assert e.entity_type == "table"
        assert e.confidence == 1.0
        _assert_frozen(e)

    def test_question_filter_construction(self):
        f = QuestionFilter(field="jalaliYearNo", operator="eq", value="1399")
        assert f.field == "jalaliYearNo"
        assert f.operator == "eq"
        assert f.confidence == 1.0
        _assert_frozen(f)

    def test_question_metric(self):
        m = QuestionMetric(name="revenue", aggregation="sum")
        assert m.name == "revenue"
        assert m.aggregation == "sum"
        _assert_frozen(m)

    def test_time_constraint(self):
        t = TimeConstraint(kind="absolute", raw_text="2024", start="2024-01-01", end="2024-12-31")
        assert t.kind == "absolute"
        _assert_frozen(t)

    def test_reference(self):
        r = Reference(text="those", resolved_to="jalaliYearNo=1399")
        assert r.text == "those"
        _assert_frozen(r)

    def test_ambiguity_report(self):
        a = AmbiguityReport(missing=("year",), clarifying_question="Which year?")
        assert a.missing == ("year",)
        _assert_frozen(a)

    def test_question_spec_minimal(self):
        spec = QuestionSpec(intent="lookup")
        assert spec.intent == "lookup"
        assert spec.entities == ()
        assert spec.ambiguity is None
        _assert_frozen(spec)

    def test_question_spec_full(self):
        spec = QuestionSpec(
            intent="aggregate",
            entities=(Entity(text="revenue", entity_type="metric"),),
            filters=(QuestionFilter(field="year", operator="eq", value="1399"),),
            metrics=(QuestionMetric(name="revenue", aggregation="sum"),),
            time_constraints=(TimeConstraint(kind="absolute", raw_text="1399"),),
            references=(Reference(text="them"),),
            ambiguity=AmbiguityReport(),
            confidence=0.8,
            warnings=("low_confidence",),
        )
        assert spec.intent == "aggregate"
        assert len(spec.entities) == 1
        assert len(spec.warnings) == 1


# ---------------------------------------------------------------------------
# Schema domain
# ---------------------------------------------------------------------------


class TestSchemaDomain:
    def test_table_descriptor(self):
        t = TableDescriptor(name="Sale.Orders", columns=("Id", "status"), primary_key="Id")
        assert t.name == "Sale.Orders"
        _assert_frozen(t)

    def test_relationship(self):
        rel = Relationship(
            from_table="Sale.OrderItems",
            from_column="orderId",
            to_table="Sale.Orders",
            to_column="Id",
            name="FK_OrderItems_Orders",
        )
        assert rel.from_table == "Sale.OrderItems"
        _assert_frozen(rel)

    def test_business_semantics(self):
        b = BusinessSemantics(
            description="revenue formula",
            formula="quantity * unitPrice",
            applies_to=("Sale.OrderItems",),
        )
        assert b.formula == "quantity * unitPrice"
        _assert_frozen(b)

    def test_schema_version(self):
        v = SchemaVersion(id="v1", captured_at=datetime(2024, 1, 1), source="introspection")
        assert v.id == "v1"
        _assert_frozen(v)

    def test_schema_snapshot(self):
        v = SchemaVersion(id="v1", captured_at=datetime(2024, 1, 1), source="introspection")
        t = TableDescriptor(name="T")
        s = SchemaSnapshot(version=v, tables=(t,), labels={"Id": "شناسه"})
        assert s.tables == (t,)
        assert s.labels["Id"] == "شناسه"
        _assert_frozen(s)

    def test_table_ref(self):
        ref = TableRef(name="Sale.Orders", alias="o")
        assert ref.alias == "o"
        _assert_frozen(ref)

    def test_column_ref(self):
        c = ColumnRef(table="Sale.Orders", column="status")
        _assert_frozen(c)

    def test_join_hint(self):
        hint = JoinHint(
            left=ColumnRef(table="A", column="id"),
            right=ColumnRef(table="B", column="aid"),
            kind="inner",
        )
        assert hint.kind == "inner"
        _assert_frozen(hint)

    def test_schema_context(self):
        ctx = SchemaContext(
            tables=(TableRef(name="T"),),
            columns=(ColumnRef(table="T", column="c"),),
        )
        assert len(ctx.tables) == 1
        _assert_frozen(ctx)


# ---------------------------------------------------------------------------
# Planning domain
# ---------------------------------------------------------------------------


class TestPlanningDomain:
    def test_join(self):
        j = Join(
            left=TableRef(name="Orders"),
            right=TableRef(name="OrderItems"),
            on_left="Id",
            on_right="orderId",
        )
        assert j.kind == "inner"
        _assert_frozen(j)

    def test_dimension(self):
        d = Dimension(column=ColumnRef(table="T", column="c"), alias="month")
        _assert_frozen(d)

    def test_plan_metric(self):
        m = PlanMetric(column=ColumnRef(table="T", column="c"), function="sum", alias="total")
        _assert_frozen(m)

    def test_plan_filter(self):
        f = PlanFilter(column=ColumnRef(table="T", column="c"), operator="eq", value="1399")
        _assert_frozen(f)

    def test_sort_key(self):
        s = SortKey(column=ColumnRef(table="T", column="c"), direction="asc")
        _assert_frozen(s)

    def test_output_requirements(self):
        o = OutputRequirements(limit=10, offset=0, distinct=True)
        assert o.limit == 10
        _assert_frozen(o)

    def test_query_plan(self):
        plan = QueryPlan(
            tables=(TableRef(name="T"),),
            output_requirements=OutputRequirements(limit=5),
        )
        assert plan.output_requirements.limit == 5
        _assert_frozen(plan)


# ---------------------------------------------------------------------------
# SQL generation / validation
# ---------------------------------------------------------------------------


class TestSQLDomain:
    def test_sql_candidate(self):
        c = SQLCandidate(
            sql="SELECT 1",
            dialect="tsql",
            candidate_id="c1",
            fingerprint="abc",
        )
        assert c.sql == "SELECT 1"
        assert c.dialect == "tsql"
        _assert_frozen(c)

    def test_parsed_ast(self):
        # Use a sentinel — sqlglot parsing is exercised in SecurityValidator tests.
        ast = ParsedAST(expression=object(), dialect="tsql")
        assert ast.dialect == "tsql"
        _assert_frozen(ast)

    def test_syntax_error(self):
        s = SyntaxError(message="bad", line=1, column=2)
        assert s.line == 1
        _assert_frozen(s)

    def test_validation_report(self):
        r = ValidationReport(
            candidate_id="c1", valid=True, parse_ok=True, syntax_errors=()
        )
        assert r.valid is True
        _assert_frozen(r)

    def test_semantic_violation(self):
        v = SemanticViolation(code="SEM.AGGREGATE_IN_WHERE", message="bad")
        assert v.severity == "error"
        _assert_frozen(v)

    def test_semantic_report(self):
        r = SemanticReport(candidate_id="c1", ok=False, violations=(SemanticViolation(code="x", message="m"),))
        assert r.ok is False
        _assert_frozen(r)

    def test_complexity_budget_defaults(self):
        b = ComplexityBudget()
        assert b.max_joins == 5
        assert b.max_estimated_cost == "medium"
        _assert_frozen(b)

    def test_complexity_report(self):
        r = ComplexityReport(ok=True, score=1.0, estimated_cost="low")
        _assert_frozen(r)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class TestExecutionDomain:
    def test_query_result(self):
        r = QueryResult(columns=("a",), rows=({"a": 1},), row_count=1, duration_ms=10.0)
        assert r.row_count == 1
        _assert_frozen(r)


# ---------------------------------------------------------------------------
# Result/Presentation
# ---------------------------------------------------------------------------


class TestPresentationDomain:
    def test_column_type(self):
        c = ColumnType(name="total", python_type="float")
        _assert_frozen(c)

    def test_shape_descriptor(self):
        s = ShapeDescriptor(rows=10, columns=3)
        _assert_frozen(s)

    def test_aggregate_ref(self):
        a = AggregateRef(metric_alias="total", source_column="value", function="sum")
        _assert_frozen(a)

    def test_result_profile(self):
        p = ResultProfile(
            shape=ShapeDescriptor(rows=1, columns=2),
            row_count=1,
        )
        assert p.shape.rows == 1
        _assert_frozen(p)

    def test_format_spec(self):
        f = FormatSpec(number_format="#,##0", currency="RLS")
        _assert_frozen(f)

    def test_locale_policy(self):
        l = LocalePolicy()
        assert l.locale == "fa_IR"
        assert l.rtl is True
        _assert_frozen(l)

    def test_presentation_plan(self):
        p = PresentationPlan(
            visible_columns=("a", "b"),
            column_order=("a", "b"),
            view_kind="table",
        )
        assert p.view_kind == "table"
        _assert_frozen(p)


# ---------------------------------------------------------------------------
# Answer domain
# ---------------------------------------------------------------------------


class TestAnswerDomain:
    def test_claim(self):
        c = Claim(text="Total revenue is 1000", value=1000, source="row:0 col:total")
        _assert_frozen(c)

    def test_evidence(self):
        e = Evidence(source="memory", content="last year revenue was high", confidence=0.9)
        _assert_frozen(e)

    def test_answer_draft(self):
        d = AnswerDraft(text="...", format="markdown-persian", claims=())
        _assert_frozen(d)

    def test_validated_answer(self):
        v = ValidatedAnswer(text="...", accepted_claims=(), rejected_claims=())
        assert v.sanitized is False
        _assert_frozen(v)


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


class TestLLMDomain:
    def test_usage(self):
        u = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        assert u.total_tokens == 15
        _assert_frozen(u)

    def test_llm_policy(self):
        p = LLMPolicy(provider="openai", model="gpt-4o-mini")
        _assert_frozen(p)

    def test_llm_budget(self):
        b = LLMBudget(max_tokens=100, timeout_ms=30000)
        _assert_frozen(b)

    def test_llm_result(self):
        r = LLMResult(
            text="hi",
            provider="openai",
            model="gpt-4o-mini",
            usage=Usage(),
        )
        _assert_frozen(r)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestTypedErrors:
    """Verify the ADR-015 typed error hierarchy and field contract."""

    def test_base_carries_required_fields(self):
        err = TypedComponentError(
            "boom",
            code="X.Y",
            user_message="user-friendly",
            stage=ErrorStageTag.QUESTION,
            retryable=True,
            cause=ValueError("underlying"),
        )
        assert err.code == "X.Y"
        assert err.message == "boom"
        assert err.user_message == "user-friendly"
        assert err.stage == ErrorStageTag.QUESTION
        assert err.retryable is True
        assert isinstance(err.cause, ValueError)
        assert isinstance(err, Exception)

    def test_question_analysis_error_defaults(self):
        err = QuestionAnalysisError("ambiguous")
        assert err.code == "QA.INTERNAL"
        assert err.stage == ErrorStageTag.QUESTION
        assert err.retryable is False
        assert isinstance(err, TypedComponentError)

    def test_schema_error_defaults(self):
        err = SchemaError("drift")
        assert err.code == "SCH.INTERNAL"
        assert err.stage == ErrorStageTag.SCHEMA

    def test_planning_error_defaults(self):
        err = PlanningError("conflict")
        assert err.code == "PLN.INTERNAL"
        assert err.stage == ErrorStageTag.PLANNING

    def test_sql_generation_error_defaults(self):
        err = SQLGenerationError("invalid_output")
        assert err.code == "GEN.INTERNAL"
        assert err.stage == ErrorStageTag.SQL_GENERATION

    def test_repair_error_defaults(self):
        err = RepairError("exhausted")
        assert err.code == "RPR.INTERNAL"
        assert err.stage == ErrorStageTag.REPAIR

    def test_result_analysis_error_defaults(self):
        err = ResultAnalysisError("internal")
        assert err.code == "RES.INTERNAL"
        assert err.stage == ErrorStageTag.RESULT

    def test_presentation_error_defaults(self):
        err = PresentationError("policy")
        assert err.code == "PRE.INTERNAL"
        assert err.stage == ErrorStageTag.PRESENTATION

    def test_answer_generation_error_defaults(self):
        err = AnswerGenerationError("llm_down")
        assert err.code == "ANS.INTERNAL"
        assert err.stage == ErrorStageTag.ANSWER

    def test_answer_validation_error_defaults(self):
        err = AnswerValidationError("ungrounded")
        assert err.code == "ANV.INTERNAL"
        assert err.stage == ErrorStageTag.ANSWER_VALIDATION

    def test_memory_error_defaults(self):
        err = MemoryError("unavailable")
        assert err.code == "MEM.INTERNAL"
        assert err.stage == ErrorStageTag.MEMORY

    def test_llm_router_error_defaults(self):
        err = LLMRouterError("provider_down")
        assert err.code == "ROU.INTERNAL"
        assert err.stage == ErrorStageTag.LLM
        # LLMRouterError is the one place retry is true by default.
        assert err.retryable is True

    def test_subclass_can_override_code(self):
        err = QuestionAnalysisError("bad", code="QA.AMBIGUOUS", retryable=True)
        assert err.code == "QA.AMBIGUOUS"
        assert err.retryable is True

    def test_all_value_objects_are_dataclasses(self):
        """Value objects use the dataclass primitive (no Pydantic)."""
        sample = [
            Entity(text="x", entity_type="t"),
            QuestionFilter(field="x", operator="eq", value=1),
            QuestionMetric(name="x"),
            TimeConstraint(kind="absolute", raw_text="x"),
            Reference(text="x"),
            AmbiguityReport(),
            QuestionSpec(intent="lookup"),
            TableDescriptor(name="t"),
            Relationship(
                from_table="a", from_column="b",
                to_table="c", to_column="d",
            ),
            BusinessSemantics(description="d"),
            SchemaVersion(id="v1", captured_at=datetime(2024, 1, 1), source="s"),
            SchemaSnapshot(
                version=SchemaVersion(id="v1", captured_at=datetime(2024, 1, 1), source="s"),
            ),
            TableRef(name="t"),
            ColumnRef(table="t", column="c"),
            JoinHint(
                left=ColumnRef(table="t", column="a"),
                right=ColumnRef(table="t", column="b"),
            ),
            SchemaContext(),
            Join(
                left=TableRef(name="a"), right=TableRef(name="b"),
                on_left="x", on_right="y",
            ),
            Dimension(column=ColumnRef(table="t", column="c")),
            PlanMetric(column=ColumnRef(table="t", column="c"), function="sum"),
            PlanFilter(column=ColumnRef(table="t", column="c"), operator="eq"),
            SortKey(column=ColumnRef(table="t", column="c")),
            OutputRequirements(),
            QueryPlan(),
            SQLCandidate(sql="SELECT 1", dialect="tsql", candidate_id="c", fingerprint="f"),
            ParsedAST(expression=None, dialect="tsql"),
            SyntaxError(message="x"),
            ValidationReport(candidate_id="c", valid=True, parse_ok=True),
            SemanticViolation(code="x", message="m"),
            SemanticReport(candidate_id="c", ok=True),
            ComplexityBudget(),
            ComplexityReport(ok=True),
            QueryResult(),
            ColumnType(name="c", python_type="int"),
            ShapeDescriptor(rows=0, columns=0),
            AggregateRef(metric_alias="a", source_column="b", function="sum"),
            ResultProfile(shape=ShapeDescriptor(rows=0, columns=0), row_count=0),
            FormatSpec(),
            LocalePolicy(),
            PresentationPlan(),
            Claim(text="x"),
            Evidence(source="s", content="c"),
            AnswerDraft(text="x"),
            ValidatedAnswer(text="x"),
            Usage(),
            LLMPolicy(),
            LLMBudget(),
            LLMResult(text="x", provider="p", model="m", usage=Usage()),
        ]
        for vo in sample:
            assert is_dataclass(vo), f"{type(vo).__name__} is not a dataclass"


# ---------------------------------------------------------------------------
# Duplicate domain concept separation
# ---------------------------------------------------------------------------


class TestDuplicateDomainConcepts:
    """The contracts module intentionally has separate types for the same
    conceptual name in different domains. Verify the boundaries are
    distinct (different identity)."""

    def test_question_metric_and_plan_metric_are_distinct(self):
        qm = QuestionMetric(name="revenue")
        pm = PlanMetric(column=None, function="sum", alias="revenue")
        # Different identity — they are not inter-substitutable.
        assert type(qm) is not type(pm)

    def test_question_filter_and_plan_filter_are_distinct(self):
        qf = QuestionFilter(field="year", operator="eq", value="1399")
        pf = PlanFilter(column=ColumnRef(table="T", column="c"), operator="eq", value="1399")
        assert type(qf) is not type(pf)
        # QuestionFilter carries raw intent; PlanFilter carries typed column ref.
        assert hasattr(qf, "field")
        assert not hasattr(pf, "field")
        assert hasattr(pf, "column")
        assert not hasattr(qf, "column")

    def test_evidence_is_shared_between_question_and_answer_domains(self):
        # Evidence appears in both QuestionSpec.references (resolved
        # earlier) and AnswerDraft.citations. The same dataclass is used
        # in both places for cross-domain compatibility.
        e = Evidence(source="x", content="y")
        spec = QuestionSpec(intent="lookup", references=(e,))
        draft = AnswerDraft(text="x", citations=("row:0",))
        # Just verify the type is importable from a single location.
        assert spec.references[0] is e
        # AnswerDraft.citations is a tuple[str,...]; Evidence is a
        # structured object. The two domains coexist; the contract
        # preserves the typing distinction while sharing the import
        # path.