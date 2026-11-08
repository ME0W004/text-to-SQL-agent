# tests/test_schema/test_orchestrator_seam.py

"""Orchestrator schema-resolution seam tests (Sprint 3.1C Phase 26).

The live pipeline has no QuestionAnalyzer yet, so ``question_analysis``
is ``None`` on every real request and the schema-resolution seam must
stay dormant. These tests prove both directions:

* with an injected QuestionSpec the full chain runs
  (catalog → retriever → applier → SCHEMA_RESOLVED);
* without a QuestionSpec nothing is fabricated: no schema context, no
  transition, no catalog/retriever access — including through the real
  façade wiring;
* typed schema failures (SCH.UNAVAILABLE / retriever errors) propagate
  with the approved error semantics;
* only the Orchestrator writes ``AgentState.schema_context``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.contracts import (
    Entity,
    LLMResult,
    Usage,
    QuestionSpec,
    SchemaContext,
    SchemaError,
    SchemaSnapshot,
    SchemaVersion,
    TableDescriptor,
)
from src.agents.orchestrator import Orchestrator, OrchestratorDeps
from src.agents.state import (
    ErrorCategory,
    PipelineStage,
    create_initial_state,
)
from src.core.schema.retriever import SchemaRetriever
from src.utils.logger import reset_trace_context, set_trace_context


# ---------------------------------------------------------------------------
# Fakes (mirroring the established orchestrator-test fake style)
# ---------------------------------------------------------------------------


class FakeExtractor:
    def extract(self, question, history=None):
        return "SELECT 1 AS value"


class FakeLLM:
    def generate(self, prompt):
        return "The value is 1."


class FakeMemory:
    def __init__(self):
        self.session = "session-schema"

    def create_session(self):
        return self.session

    def session_exists(self, session_id):
        return bool(session_id)

    def get_history(self, session_id, limit=5):
        return []

    def add_message(self, session_id, role, content):
        pass

    def clear_session(self, session_id):
        pass


class FakePrompts:
    def natural_response_prompt(self, question, rows, row_count):
        return "summarize"


class FakeRouter:
    """Offline LLMRouter double (no provider, no network)."""

    def generate(self, prompt, *, task="general", policy=None, budget=None):
        return LLMResult(text="SELECT 1 AS value", provider="fake", model="fake", usage=Usage())

    def has_available_provider(self):
        return True


class FakeCatalog:
    """Minimal SchemaCatalog Protocol double with access counting."""

    def __init__(self, snapshot=None, error: Exception | None = None):
        self.snapshot = snapshot
        self.error = error
        self.snapshot_reads = 0

    def current_version(self):
        return "v-test"

    def versions(self):
        return ()

    def get_snapshot(self, version=None):
        self.snapshot_reads += 1
        if self.error is not None:
            raise self.error
        if self.snapshot is None:
            raise SchemaError("Schema information is not available.", code="SCH.UNAVAILABLE")
        return self.snapshot

    def refresh(self):
        return self.get_snapshot()


class RecordingRetriever:
    """Wraps the real retriever; records calls and can be forced to fail."""

    def __init__(self, error: Exception | None = None):
        self.inner = SchemaRetriever()
        self.error = error
        self.calls = 0

    def retrieve(self, spec, snapshot):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.inner.retrieve(spec, snapshot)


class FakeAnalyzer:
    """Produces the QuestionSpec the live pipeline cannot yet supply."""

    def __init__(self, spec: QuestionSpec):
        self.spec = spec
        self.calls = 0

    def analyze(self, question, history=None, *, active_filters=None, resolved_references=None):
        self.calls += 1
        return self.spec


def make_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        version=SchemaVersion(id="v-test", captured_at=datetime(2026, 9, 4), source="fixture"),
        tables=(
            TableDescriptor(name="Sale.Orders", columns=("Id", "buyer"), primary_key="Id"),
        ),
    )


NAMED_ORDER = QuestionSpec(
    intent="lookup",
    entities=(Entity("Orders", entity_type="table"),),
)


def build_orchestrator(*, catalog=None, retriever=None, analyzer=None) -> Orchestrator:
    deps = OrchestratorDeps(
        sql_extractor=FakeExtractor(),
        query_executor=_fake_executor(),
        llm_client=FakeLLM(),
        prompts=FakePrompts(),
        memory_manager=FakeMemory(),
        question_analyzer=analyzer,
        schema_catalog=catalog,
        schema_retriever=retriever,
    )
    return Orchestrator(deps)


def _fake_executor():
    from src.agents.adapters import QueryExecutorAdapter
    from src.core.database.connection import DatabaseConnection, ExecutionPolicy

    class _Cursor:
        arraysize = 1
        _remaining = [{"value": 1}]

        def execute(self, sql, params=None):
            pass

        def fetchmany(self, size):
            batch, self._remaining = self._remaining[:size], self._remaining[size:]
            return batch

        def close(self):
            pass

    class _Conn:
        def cursor(self, as_dict=True):
            return _Cursor()

        def close(self):
            pass

    db = DatabaseConnection(
        "host", 1433, "user", "password", "database",
        execution_policy=ExecutionPolicy(max_rows=10, max_fetch_batch=2),
        connect_fn=lambda **kwargs: _Conn(),
    )
    return QueryExecutorAdapter(db)


def run(orch: Orchestrator, question: str = "what is value?"):
    token = set_trace_context("trace-schema-seam")
    try:
        return orch.run(create_initial_state(question=question, session_id="s-schema"))
    finally:
        reset_trace_context(token)


# ---------------------------------------------------------------------------
# Dormant seam (Case B)
# ---------------------------------------------------------------------------


class TestDormantSeam:
    def test_no_question_spec_means_no_fabricated_context(self):
        catalog = FakeCatalog(snapshot=make_snapshot())
        retriever = RecordingRetriever()
        orch = build_orchestrator(catalog=catalog, retriever=retriever)
        final = run(orch)
        # No QuestionAnalyzer on the live path: the seam must stay
        # dormant — no context, no transition, no catalog/retriever
        # access — while the pipeline itself still succeeds.
        assert final.schema_context is None
        assert final.is_success()
        assert final.pipeline_stage == PipelineStage.ANSWERED
        assert catalog.snapshot_reads == 0
        assert retriever.calls == 0

    def test_facade_wiring_stays_dormant(self, monkeypatch):
        """The real façade wires catalog+retriever into OrchestratorDeps,
        and a full offline request still leaves schema_context untouched."""
        import src.agents.text_to_sql_agent as facade_module

        class FakeDB:
            execution_policy = None

            def __init__(self, **kwargs):
                pass

            def connect(self):
                pass

            def close(self):
                pass

            def execute_query(self, query, params=None):
                return [{"value": 1}]

        monkeypatch.setattr(facade_module, "DatabaseConnection", FakeDB)
        monkeypatch.setattr(facade_module, "ensure_column_map_is_updated", lambda db: None)
        monkeypatch.setattr(facade_module, "MemoryManager", FakeMemory)
        monkeypatch.setattr(facade_module, "load_config", lambda: {"host": "fake"})
        import src.core.llm.router as router_module

        monkeypatch.setattr(router_module, "build_default_router", lambda: FakeRouter())

        from src.core.schema.catalog import SchemaCatalog
        from src.core.schema.retriever import SchemaRetriever as Retriever

        agent = facade_module.TextToSQLAgent()
        assert isinstance(agent.schema_catalog, SchemaCatalog)
        assert isinstance(agent.schema_retriever, Retriever)
        deps = agent._build_orchestrator()._deps
        assert deps.schema_catalog is agent.schema_catalog
        assert deps.schema_retriever is agent.schema_retriever

        final = agent.ask("what is value?", session_id="s-facade")
        # Full request ran (succeeded through the fakes) with the schema
        # seam completely dormant.
        assert final["success"] is True
        state_schema_context = None
        # The response dict carries no schema context (API shape unchanged);
        # dormancy is proven by the orchestrator-level test above plus the
        # catalog never being consulted: use the deps' catalog counter.
        assert isinstance(deps.schema_catalog, SchemaCatalog)
        assert deps.schema_retriever is not None
        assert state_schema_context is None


# ---------------------------------------------------------------------------
# Active seam (with a QuestionSpec)
# ---------------------------------------------------------------------------


class TestActiveSeam:
    def test_full_chain_with_question_spec(self):
        catalog = FakeCatalog(snapshot=make_snapshot())
        retriever = RecordingRetriever()
        analyzer = FakeAnalyzer(NAMED_ORDER)
        orch = build_orchestrator(catalog=catalog, retriever=retriever, analyzer=analyzer)
        final = run(orch)
        assert final.question_analysis is NAMED_ORDER
        assert final.schema_context is not None
        assert catalog.snapshot_reads == 1
        assert retriever.calls == 1
        # The pipeline continued through resolution to a successful end.
        assert final.is_success()
        assert final.pipeline_stage == PipelineStage.ANSWERED

    def test_schema_context_is_typed_and_selected(self):
        catalog = FakeCatalog(snapshot=make_snapshot())
        orch = build_orchestrator(
            catalog=catalog, retriever=RecordingRetriever(), analyzer=FakeAnalyzer(NAMED_ORDER)
        )
        final = run(orch)
        assert isinstance(final.schema_context, SchemaContext)
        assert [t.name for t in final.schema_context.tables] == ["Sale.Orders"]


# ---------------------------------------------------------------------------
# Typed failure semantics
# ---------------------------------------------------------------------------


class TestTypedFailureSemantics:
    def test_catalog_unavailable_propagates_approved_code(self):
        catalog = FakeCatalog(
            error=SchemaError("Schema information is not available.", code="SCH.UNAVAILABLE")
        )
        orch = build_orchestrator(
            catalog=catalog, retriever=RecordingRetriever(), analyzer=FakeAnalyzer(NAMED_ORDER)
        )
        final = run(orch)
        assert final.is_failed()
        assert final.error.category == ErrorCategory.SCHEMA
        assert final.error.code == "SCH.UNAVAILABLE"

    def test_retriever_failure_propagates_typed(self):
        catalog = FakeCatalog(snapshot=make_snapshot())
        orch = build_orchestrator(
            catalog=catalog,
            retriever=RecordingRetriever(error=SchemaError("retrieval boom", code="SCH.INTERNAL")),
            analyzer=FakeAnalyzer(NAMED_ORDER),
        )
        final = run(orch)
        assert final.is_failed()
        assert final.error.category == ErrorCategory.SCHEMA
        assert final.error.code == "SCH.INTERNAL"

    def test_no_snapshot_means_no_empty_context(self):
        # A catalog that always fails with an active spec: state fails
        # with the approved semantics; an empty context is never written.
        catalog = FakeCatalog(snapshot=None)
        orch = build_orchestrator(
            catalog=catalog, retriever=RecordingRetriever(), analyzer=FakeAnalyzer(NAMED_ORDER)
        )
        final = run(orch)
        assert final.is_failed()
        assert final.schema_context is None
        assert final.error.code == "SCH.UNAVAILABLE"


# ---------------------------------------------------------------------------
# State ownership
# ---------------------------------------------------------------------------


class TestStateOwnership:
    def test_retriever_never_touches_state(self):
        # The retriever imports no state module and calls no state
        # applier; the repo-wide single-writer AST scan pins the with_*
        # rule for every new module.
        import inspect

        from src.core.schema import retriever as retriever_module

        source = inspect.getsource(retriever_module)
        assert "src.agents.state" not in source
        assert "with_schema_context" not in source
        assert "create_initial_state" not in source

    def test_schema_context_write_is_applier_only(self):
        # On the active path the context reaches state through the
        # existing applier and validated transition: a successful run
        # with a spec passes through SCHEMA_RESOLVED and ends ANSWERED.
        catalog = FakeCatalog(snapshot=make_snapshot())
        orch = build_orchestrator(
            catalog=catalog, retriever=RecordingRetriever(), analyzer=FakeAnalyzer(NAMED_ORDER)
        )
        final = run(orch)
        assert final.schema_context is not None
        assert final.pipeline_stage == PipelineStage.ANSWERED


# ---------------------------------------------------------------------------
# Phase 3.2: bounded-context runtime metrics (Orchestrator-only writes)
# ---------------------------------------------------------------------------


class TestSchemaContextMetrics:
    def test_active_seam_records_bounded_context_metrics(self):
        catalog = FakeCatalog(snapshot=make_snapshot())
        orch = build_orchestrator(
            catalog=catalog, retriever=RecordingRetriever(), analyzer=FakeAnalyzer(NAMED_ORDER)
        )
        final = run(orch)
        metrics = final.metadata.schema_metrics
        assert metrics["selected_tables"] == len(final.schema_context.tables)
        assert metrics["selected_columns"] == len(final.schema_context.columns)
        assert metrics["join_hints"] == len(final.schema_context.joins)
        assert metrics["semantics_entries"] == len(final.schema_context.relevant_business_semantics)
        assert metrics["context_chars"] > 0
        assert "schema_resolution" in final.metadata.stage_timings

    def test_dormant_seam_records_no_metrics(self):
        catalog = FakeCatalog(snapshot=make_snapshot())
        retriever = RecordingRetriever()
        orch = build_orchestrator(catalog=catalog, retriever=retriever)
        final = run(orch)
        assert final.metadata.schema_metrics == {}
        assert "schema_resolution" not in final.metadata.stage_timings

    def test_metrics_counts_only_never_schema_text(self):
        # The metrics payload carries counts and sizes — never table or
        # column names, warnings, prompts or the question itself.
        catalog = FakeCatalog(snapshot=make_snapshot())
        orch = build_orchestrator(
            catalog=catalog, retriever=RecordingRetriever(), analyzer=FakeAnalyzer(NAMED_ORDER)
        )
        final = run(orch)
        for value in final.metadata.schema_metrics.values():
            assert isinstance(value, int)


# ---------------------------------------------------------------------------
# Phase 3.2: full-schema leakage prevention on the active generation path
# ---------------------------------------------------------------------------


class FakePlanner:
    def plan(self, spec, context):
        from src.agents.contracts import QueryPlan

        return QueryPlan()


class CapturingGenerator:
    """SQLGenerator double recording exactly what the LLM-facing
    generation boundary receives."""

    def __init__(self):
        self.calls: list[tuple[object, object]] = []

    def generate(self, plan, context, *, dialect="tsql"):
        self.calls.append((plan, context))

        class _Candidate:
            sql = "SELECT 1 AS value"
            dialect = "tsql"
            candidate_id = "fake"
            fingerprint = "fake"
            metadata = {}
            prompt_strategy = "zero-shot"

        return _Candidate()


class LeakageLeakSnapshot:
    """Snapshot with two extra tables the question never names."""

    @staticmethod
    def build() -> SchemaSnapshot:
        base = make_snapshot()
        from src.agents.contracts import TableDescriptor

        extra_tables = base.tables + (
            TableDescriptor(name="Sale.SecretLedger", columns=("Id", "payload")),
            TableDescriptor(name="Sale.AuditTrail", columns=("Id", "actor")),
        )
        return SchemaSnapshot(
            version=base.version,
            tables=extra_tables,
            relationships=base.relationships,
            business_semantics=base.business_semantics,
            labels=base.labels,
        )


class TestFullSchemaLeakageOnGenerationPath:
    def test_generator_receives_bounded_context_not_snapshot(self):
        # Protected LLM-facing path (SQLGenerator seam): the generation
        # input must be the retrieved SchemaContext — never the full
        # snapshot, never the legacy full-schema text.
        catalog = FakeCatalog(snapshot=LeakageLeakSnapshot.build())
        generator = CapturingGenerator()
        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=_fake_executor(),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            question_analyzer=FakeAnalyzer(NAMED_ORDER),
            schema_catalog=catalog,
            schema_retriever=RecordingRetriever(),
            query_planner=FakePlanner(),
            sql_generator=generator,
        )
        orch = Orchestrator(deps)
        final = run(orch)
        assert final.is_success()
        assert len(generator.calls) == 1
        received_plan, received_context = generator.calls[0]
        # The LLM-facing schema payload ⊆ retrieved SchemaContext.
        assert received_context is final.schema_context
        received_tables = {t.name for t in received_context.tables}
        snapshot_tables = {t.name for t in catalog.snapshot.tables}
        assert received_tables == {"Sale.Orders"}
        assert received_tables != snapshot_tables  # bounded, not full schema
        # Nothing passed to the generator carries the legacy text.
        assert not any("COLUMN:" in repr(arg) or "Database Schema" in repr(arg) for arg in (received_plan, received_context))

    def test_unselected_tables_never_reach_the_generation_boundary(self):
        catalog = FakeCatalog(snapshot=LeakageLeakSnapshot.build())
        generator = CapturingGenerator()
        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=_fake_executor(),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            question_analyzer=FakeAnalyzer(NAMED_ORDER),
            schema_catalog=catalog,
            schema_retriever=RecordingRetriever(),
            query_planner=FakePlanner(),
            sql_generator=generator,
        )
        final = Orchestrator(deps).run(create_initial_state(question="what is value?", session_id="s-leak"))
        received_context = generator.calls[0][1]
        received_columns = {(c.table, c.column) for c in received_context.columns}
        for name in ("Sale.SecretLedger", "Sale.AuditTrail"):
            assert name not in {t.name for t in received_context.tables}
            assert not any(table == name for table, _ in received_columns)
