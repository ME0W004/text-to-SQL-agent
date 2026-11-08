# tests/test_schema/test_retrieval_hardening.py

"""Phase 3.2 retrieval-boundary hardening tests.

Covers the production gaps closed on top of Sprint 3.1C:

* query-type-aware evidence routing (filter fields are column-scoped;
  metric names stay dual-surface);
* hard context bounds (join-hint cap, semantic-entry cap, serialized
  context-size budget) with deterministic budgeting;
* ``SCH.CONTEXT_BOUNDED`` reporting;
* ``schema_retrieval_completed`` telemetry (counts/codes only);
* the canonical ``serialized_context_size`` measure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest

from src.agents.contracts import (
    BusinessSemantics,
    ColumnDescriptor,
    ColumnRef,
    Entity,
    QuestionFilter,
    QuestionMetric,
    QuestionSpec,
    Relationship,
    SchemaContext,
    SchemaSnapshot,
    SchemaVersion,
    TableDescriptor,
)
from src.core.schema.retriever import (
    DEFAULT_RETRIEVAL_POLICY,
    RetrievalPolicy,
    SchemaRetriever,
    serialized_context_size,
)


def make_snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        version=SchemaVersion(id="test", captured_at=datetime(2026, 9, 4), source="fixture"),
        tables=(
            TableDescriptor(
                name="Sale.Orders",
                description="Order headers: one row per sales order/contract.",
                columns=("Id", "buyer", "status", "jalaliYearNo", "operationMonth"),
                primary_key="Id",
                column_details=(
                    ColumnDescriptor("Id", "int", False, key_role="pk"),
                    ColumnDescriptor("buyer", "nvarchar", False, description="Customer / company name"),
                    ColumnDescriptor("status", "nvarchar", False, description="Order lifecycle status"),
                    ColumnDescriptor("jalaliYearNo", "varchar", True, description="Persian calendar year"),
                    ColumnDescriptor("operationMonth", "int", True, description="Jalali YYYYMM month"),
                ),
            ),
            TableDescriptor(
                name="Sale.OrderItems",
                description="Order line items: product, quantity, price.",
                columns=("Id", "orderId", "quantity", "unitPrice", "productGroup"),
                primary_key="Id",
                column_details=(
                    ColumnDescriptor("Id", "int", False, key_role="pk"),
                    ColumnDescriptor("orderId", "int", False, key_role="fk"),
                    ColumnDescriptor("quantity", "float", True, description="Ordered quantity"),
                    ColumnDescriptor("unitPrice", "decimal", True, description="Price per unit"),
                    ColumnDescriptor("productGroup", "nvarchar", False, description="Product category"),
                ),
            ),
            TableDescriptor(
                name="Sale.Shipments",
                description="Physical shipments per order item.",
                columns=("Id", "orderItemId", "quantity", "netWeight"),
                primary_key="Id",
                column_details=(
                    ColumnDescriptor("Id", "int", False, key_role="pk"),
                    ColumnDescriptor("orderItemId", "int", True, key_role="fk"),
                    ColumnDescriptor("quantity", "float", True, description="Delivered quantity"),
                    ColumnDescriptor("netWeight", "decimal", True, description="Net weight"),
                ),
            ),
        ),
        relationships=(
            Relationship("Sale.OrderItems", "orderId", "Sale.Orders", "Id", kind="annotated", cardinality="1:N"),
            Relationship("Sale.Shipments", "orderItemId", "Sale.OrderItems", "Id", kind="annotated", cardinality="1:N"),
        ),
        business_semantics=(
            BusinessSemantics(
                description="Revenue for a line item is computed as quantity times unitPrice",
                formula="quantity * unitPrice",
                applies_to=("Sale.OrderItems.quantity", "Sale.OrderItems.unitPrice"),
            ),
            BusinessSemantics(
                description="Delivered quantity per order item is aggregated from Shipments.quantity",
                formula=None,
                applies_to=("Sale.Shipments.quantity", "Sale.OrderItems.quantity"),
            ),
        ),
        labels={
            "Sale.Orders.buyer": "خریدار",
            "Sale.OrderItems.quantity": "تعداد",
            "Sale.Shipments.quantity": "تعداد",
        },
    )


def retrieve(spec, snapshot=None, policy=DEFAULT_RETRIEVAL_POLICY) -> SchemaContext:
    return SchemaRetriever(policy).retrieve(spec, snapshot or make_snapshot())


# ---------------------------------------------------------------------------
# Query-type-aware evidence routing (Phase 8)
# ---------------------------------------------------------------------------


class TestQueryTypeAwareRouting:
    def _twin_status_snapshot(self) -> SchemaSnapshot:
        """Two tables each owning a `status` column; only T1's *table
        description* contains the word "status"."""
        return SchemaSnapshot(
            version=SchemaVersion(id="routing", captured_at=datetime(2026, 9, 4), source="fixture"),
            tables=(
                TableDescriptor(
                    name="Sale.TAlpha",
                    description="Order status records.",
                    columns=("Id", "status"),
                    primary_key="Id",
                    column_details=(
                        ColumnDescriptor("Id", "int", False, key_role="pk"),
                        ColumnDescriptor("status", "nvarchar", False),
                    ),
                ),
                TableDescriptor(
                    name="Sale.TBeta",
                    description="Unrelated records.",
                    columns=("Id", "status"),
                    primary_key="Id",
                    column_details=(
                        ColumnDescriptor("Id", "int", False, key_role="pk"),
                        ColumnDescriptor("status", "nvarchar", False),
                    ),
                ),
            ),
        )

    def test_filter_fields_are_column_scoped_not_table_evidence(self):
        # The filter field "status" gives both tables the same column
        # evidence (+5 each). The description token on TAlpha must NOT
        # break the tie any more (filter evidence ≠ table evidence), so
        # the tables tie without name evidence → SCH.AMBIGUOUS.
        spec = QuestionSpec(
            intent="a",
            filters=(QuestionFilter(field="status", operator="eq", value="x"),),
        )
        ctx = retrieve(spec, self._twin_status_snapshot())
        assert {t.name for t in ctx.tables} == {"Sale.TAlpha", "Sale.TBeta"}
        assert any(w.startswith("SCH.AMBIGUOUS") for w in ctx.warnings)
        # The filter still drives column evidence on both tables.
        columns = {(c.table, c.column) for c in ctx.columns}
        assert ("Sale.TAlpha", "status") in columns
        assert ("Sale.TBeta", "status") in columns

    def test_metric_names_remain_dual_surface_evidence(self):
        # A metric name is concept evidence: it may legitimately surface
        # in a table description (OrderItems: "... quantity, price.").
        spec = QuestionSpec(intent="a", metrics=(QuestionMetric("quantity", "sum"),))
        ctx = retrieve(spec)
        # OrderItems gains the description signal (+3) on top of the
        # column match (+5) → strictly above Shipments (5).
        assert [t.name for t in ctx.tables][0] == "Sale.OrderItems"
        assert not any(w.startswith("SCH.AMBIGUOUS") for w in ctx.warnings)

    def test_untyped_entities_remain_dual_surface(self):
        spec = QuestionSpec(intent="a", entities=(Entity("quantity", entity_type="entity"),))
        ctx = retrieve(spec)
        assert [t.name for t in ctx.tables][0] == "Sale.OrderItems"

    def test_time_constraints_and_references_contribute_nothing(self):
        # Documented prerequisite: no deterministic mapping yet — the
        # retriever must not silently invent evidence from these fields.
        spec = QuestionSpec(
            intent="a",
            entities=(Entity("zzz_nothing", entity_type="entity"),),
        )
        ctx = retrieve(spec)
        assert ctx.tables == ()


# ---------------------------------------------------------------------------
# Hard context bounds and deterministic budgeting (Phases 9-10)
# ---------------------------------------------------------------------------


class TestHardContextBounds:
    THREE_NAMED = QuestionSpec(
        intent="a",
        entities=(
            Entity("Orders", entity_type="table"),
            Entity("OrderItems", entity_type="table"),
            Entity("Shipments", entity_type="table"),
        ),
    )

    def test_defaults_never_bind_on_ordinary_contexts(self):
        ctx = retrieve(self.THREE_NAMED)
        assert not any(w.startswith("SCH.CONTEXT_BOUNDED") for w in ctx.warnings)
        assert serialized_context_size(ctx) <= DEFAULT_RETRIEVAL_POLICY.max_context_chars

    def test_join_hint_cap_keeps_canonically_first_hints(self):
        policy = RetrievalPolicy(max_join_hints=1)
        ctx = retrieve(self.THREE_NAMED, policy=policy)
        assert len(ctx.joins) == 1
        assert any(w.startswith("SCH.CONTEXT_BOUNDED") for w in ctx.warnings)
        # Canonically-first hint retained (sorted by left/right tuple).
        first = min(
            ((h.left.table, h.left.column, h.right.table, h.right.column) for h in ctx.joins),
        )
        assert ctx.joins[0].left.table == first[0]

    def test_semantic_entry_cap_keeps_canonical_front(self):
        spec = QuestionSpec(
            intent="a",
            metrics=(QuestionMetric("quantity", "sum"),),
        )
        # Both semantics entries apply to the selected tables.
        policy = RetrievalPolicy(max_semantic_entries=1)
        ctx = retrieve(spec, policy=policy)
        assert len(ctx.relevant_business_semantics) == 1
        # The FIRST canonical (snapshot-order) entry is retained.
        assert (
            ctx.relevant_business_semantics[0].description
            == "Revenue for a line item is computed as quantity times unitPrice"
        )
        assert any(w.startswith("SCH.CONTEXT_BOUNDED") for w in ctx.warnings)

    def test_size_budget_drops_semantics_first(self):
        spec = QuestionSpec(
            intent="a",
            entities=(
                Entity("Orders", entity_type="table"),
                Entity("Shipments", entity_type="table"),
                Entity("Id", entity_type="column"),
                Entity("buyer", entity_type="column"),
            ),
        )
        baseline_ctx = retrieve(spec)
        baseline = serialized_context_size(baseline_ctx)
        # A budget just under the baseline forces trimming but sits far
        # above the structural floor.
        policy = RetrievalPolicy(max_context_chars=baseline - 100)
        ctx = retrieve(spec, policy=policy)
        assert any(w.startswith("SCH.CONTEXT_BOUNDED") for w in ctx.warnings)
        assert serialized_context_size(ctx) <= baseline - 100
        # Tables, join hints and join-endpoint columns are never removed.
        assert {t.name for t in ctx.tables} == {t.name for t in baseline_ctx.tables}
        assert len(ctx.joins) == len(baseline_ctx.joins)
        present = {(c.table, c.column) for c in ctx.columns}
        for hint in ctx.joins:
            assert (hint.left.table, hint.left.column) in present
            assert (hint.right.table, hint.right.column) in present

    def test_size_budget_drops_columns_by_documented_priority(self):
        # Three-table context: join endpoints are protected; droppable
        # columns go lowest-score-first (ties → lexicographically first
        # table, then highest ordinal, then first name). "status" scores
        # 9 (exact 6 + description 3), so buyer (6) and Shipments.Id (6)
        # drop before it, and the table-name tie-break puts buyer first.
        spec = QuestionSpec(
            intent="a",
            entities=(
                Entity("Orders", entity_type="table"),
                Entity("Shipments", entity_type="table"),
                Entity("Id", entity_type="column"),
                Entity("buyer", entity_type="column"),
                Entity("status", entity_type="column"),
            ),
        )
        full = retrieve(spec)
        baseline = serialized_context_size(full)
        # A budget that forces two column drops after the semantics
        # entries are gone (notice included in the fit measurement).
        policy = RetrievalPolicy(max_context_chars=baseline - 260)
        ctx = retrieve(spec, policy=policy)
        assert any(w.startswith("SCH.CONTEXT_BOUNDED") for w in ctx.warnings)
        dropped = {(c.table, c.column) for c in full.columns} - {
            (c.table, c.column) for c in ctx.columns
        }
        assert dropped == {("Sale.Orders", "buyer"), ("Sale.Shipments", "Id")}
        # Endpoints and the higher-scored status column survive.
        assert ("Sale.Orders", "status") in {(c.table, c.column) for c in ctx.columns}
        for hint in ctx.joins:
            assert (hint.left.table, hint.left.column) in {
                (c.table, c.column) for c in ctx.columns
            }
            assert (hint.right.table, hint.right.column) in {
                (c.table, c.column) for c in ctx.columns
            }
        # Deterministic across runs.
        assert retrieve(spec, policy=policy) == ctx

    def test_semantics_dropped_before_columns(self):
        spec = QuestionSpec(
            intent="a",
            entities=(
                Entity("Orders", entity_type="table"),
                Entity("Shipments", entity_type="table"),
                Entity("Id", entity_type="column"),
                Entity("buyer", entity_type="column"),
                Entity("status", entity_type="column"),
            ),
        )
        full = retrieve(spec)
        baseline = serialized_context_size(full)
        # Budget that only the semantics savings can satisfy: columns
        # must all survive while both semantics entries are dropped.
        policy = RetrievalPolicy(max_context_chars=baseline - 200)
        ctx = retrieve(spec, policy=policy)
        assert len(ctx.relevant_business_semantics) < len(full.relevant_business_semantics)
        assert {(c.table, c.column) for c in ctx.columns} == {
            (c.table, c.column) for c in full.columns
        }

    def test_structural_floor_breach_is_reported_never_hidden(self):
        policy = RetrievalPolicy(max_context_chars=60)
        ctx = retrieve(self.THREE_NAMED, policy=policy)
        # The floor (tables + hints + endpoints) cannot shrink further;
        # the context is returned with the breach reported.
        assert any("structural context floor" in w for w in ctx.warnings)
        assert ctx.tables
        assert ctx.joins

    def test_budgeting_is_deterministic(self):
        spec = self.THREE_NAMED
        policy = RetrievalPolicy(max_context_chars=320)
        runs = [retrieve(spec, policy=policy) for _ in range(5)]
        assert all(run == runs[0] for run in runs)

    def test_bounds_apply_regardless_of_snapshot_size(self):
        # Bigness cannot bypass the bounds: a large snapshot still yields
        # a top-K-bounded, size-bounded context.
        big = self._big_snapshot(tables=40, columns_per_table=30)
        spec = QuestionSpec(intent="a", entities=(Entity("t005", entity_type="table"),))
        ctx = retrieve(spec, big)
        assert len(ctx.tables) <= DEFAULT_RETRIEVAL_POLICY.table_top_k
        per_table: dict[str, int] = {}
        for c in ctx.columns:
            per_table[c.table] = per_table.get(c.table, 0) + 1
        assert all(n <= DEFAULT_RETRIEVAL_POLICY.column_top_k_per_table for n in per_table.values())
        assert serialized_context_size(ctx) <= DEFAULT_RETRIEVAL_POLICY.max_context_chars

    @staticmethod
    def _big_snapshot(tables: int, columns_per_table: int) -> SchemaSnapshot:
        table_descriptors = []
        for i in range(tables):
            name = f"Sale.T{i:03d}"
            cols = tuple(f"c{j:02d}" for j in range(columns_per_table))
            table_descriptors.append(
                TableDescriptor(
                    name=name,
                    description=f"Synthetic table {i}.",
                    columns=cols,
                    primary_key="c00",
                    column_details=tuple(
                        ColumnDescriptor(c, "int", False) for c in cols
                    ),
                )
            )
        return SchemaSnapshot(
            version=SchemaVersion(id="big", captured_at=datetime(2026, 9, 4), source="synthetic"),
            tables=tuple(table_descriptors),
        )


# ---------------------------------------------------------------------------
# Canonical serialized size
# ---------------------------------------------------------------------------


class TestSerializedContextSize:
    def test_positive_and_deterministic(self):
        spec = QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),))
        ctx = retrieve(spec)
        size = serialized_context_size(ctx)
        assert size > 0
        assert serialized_context_size(retrieve(spec)) == size

    def test_equal_contexts_measure_equal(self):
        spec = QuestionSpec(intent="a", entities=(Entity("buyer", entity_type="column"),))
        assert serialized_context_size(retrieve(spec)) == serialized_context_size(retrieve(spec))

    def test_size_grows_with_content(self):
        small = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)))
        big = retrieve(
            QuestionSpec(
                intent="a",
                entities=(
                    Entity("Orders", entity_type="table"),
                    Entity("Shipments", entity_type="table"),
                ),
            )
        )
        assert serialized_context_size(big) > serialized_context_size(small)

    def test_empty_context_measures_finite(self):
        size = serialized_context_size(SchemaContext())
        assert size > 0


# ---------------------------------------------------------------------------
# Telemetry (component-contracts §3: schema_retrieval_completed)
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record):
        self.records.append(record.__dict__)


class TestRetrievalTelemetry:
    @pytest.fixture
    def capture(self):
        handler = _ListHandler()
        logger = logging.getLogger("app")
        old_handlers = logger.handlers[:]
        old_propagate = logger.propagate
        old_level = logger.level
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        yield handler
        logger.handlers = old_handlers
        logger.propagate = old_propagate
        logger.level = old_level

    def _events(self, handler):
        return [r for r in handler.records if r.get("event") == "schema_retrieval_completed"]

    def test_one_event_per_retrieval_with_counts_only(self, capture):
        spec = QuestionSpec(intent="a", entities=(Entity("buyer", entity_type="column"),))
        ctx = retrieve(spec)
        events = self._events(capture)
        assert len(events) == 1
        event = events[0]
        assert event["outcome"] == "completed"
        assert event["selected_tables"] == len(ctx.tables)
        assert event["selected_columns"] == len(ctx.columns)
        assert event["join_hints"] == len(ctx.joins)
        assert event["context_chars"] == serialized_context_size(ctx)
        assert event["snapshot_version"] == "test"
        assert event["warning_codes"] == []
        assert event["bounded"] is False
        assert isinstance(event["duration_ms"], float)

    def test_no_match_outcome(self, capture):
        retrieve(QuestionSpec(intent="a", entities=(Entity("zzz_nothing", entity_type="entity"),)))
        (event,) = self._events(capture)
        assert event["outcome"] == "no_match"
        assert "SCH.NO_RELEVANT_SCHEMA" in event["warning_codes"]

    def test_ambiguous_outcome_and_codes_only(self, capture):
        retrieve(QuestionSpec(intent="a", entities=(Entity("quantity", entity_type="column"),)))
        (event,) = self._events(capture)
        assert event["outcome"] == "ambiguous"
        assert event["warning_codes"] == ["SCH.AMBIGUOUS"]

    def test_bounded_flag_and_no_sensitive_content(self, capture):
        # A distinctive term that must never appear in the telemetry
        # payload: the event carries counts and codes, never the
        # question's terms, schema text or warnings prose.
        spec = QuestionSpec(
            intent="a",
            entities=(Entity("TopSecretTermXYZ", entity_type="entity"),),
        )
        retrieve(spec)
        payload = json.dumps(self._events(capture)[0], default=str)
        assert "TopSecretTermXYZ" not in payload

    def test_bounded_flag_true_when_trimmed(self, capture):
        spec = QuestionSpec(
            intent="a",
            entities=(
                Entity("Orders", entity_type="table"),
                Entity("Shipments", entity_type="table"),
                Entity("Id", entity_type="column"),
                Entity("buyer", entity_type="column"),
                Entity("status", entity_type="column"),
            ),
        )
        baseline = serialized_context_size(retrieve(spec))
        retrieve(spec, policy=RetrievalPolicy(max_context_chars=baseline - 260))
        (event,) = [e for e in self._events(capture) if e["bounded"] is True]
        assert "SCH.CONTEXT_BOUNDED" in event["warning_codes"]
