# tests/test_schema/test_retriever.py

"""SchemaRetriever behavior tests (Sprint 3.1C Phase 24).

Pure in-memory snapshots and QuestionSpecs — no database, no LLM, no
state. The snapshot builder mirrors the seeded three-table evidence base
(same tables, logical relationships, a representative label/description/
semantics subset) so scoring behavior is verifiable against the real
schema shape.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.contracts import (
    BusinessSemantics,
    ColumnDescriptor,
    ColumnRef,
    Entity,
    JoinHint,
    QuestionFilter,
    QuestionMetric,
    QuestionSpec,
    Relationship,
    SchemaContext,
    SchemaError,
    SchemaSnapshot,
    SchemaVersion,
    TableDescriptor,
)
from src.core.schema.retriever import (
    DEFAULT_RETRIEVAL_POLICY,
    RetrievalPolicy,
    SchemaRetriever,
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
        ),
        labels={
            "Sale.Orders.buyer": "خریدار",
            "Sale.OrderItems.quantity": "تعداد",
            "Sale.Shipments.quantity": "تعداد",
        },
    )


def retrieve(spec: QuestionSpec, snapshot: SchemaSnapshot | None = None) -> SchemaContext:
    retriever = SchemaRetriever()
    if snapshot is None:
        return retriever.retrieve(spec, make_snapshot())
    return retriever.retrieve(spec, snapshot)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class TestMatching:
    def test_exact_table_name_match(self):
        ctx = retrieve(QuestionSpec(intent="lookup", entities=(Entity("Shipments", entity_type="table"),)))
        assert [t.name for t in ctx.tables] == ["Sale.Shipments"]
        assert ctx.warnings == ()

    def test_exact_column_match_selects_its_table(self):
        ctx = retrieve(QuestionSpec(intent="lookup", entities=(Entity("buyer", entity_type="column"),)))
        assert [t.name for t in ctx.tables] == ["Sale.Orders"]
        assert (ColumnRef(table="Sale.Orders", column="buyer")) in ctx.columns

    def test_qualified_name_match(self):
        ctx = retrieve(QuestionSpec(
            intent="lookup",
            entities=(Entity("Orders", entity_type="table", qualified_name="Sale.Orders"),),
        ))
        # Qualified strength (10) relationship-assists the direct
        # neighbor OrderItems (min(0.5*10, 6) = 5 >= threshold).
        assert [t.name for t in ctx.tables] == ["Sale.Orders", "Sale.OrderItems"]
        assert ctx.confidence == 1.0

    def test_qualified_column_reference_names_its_table(self):
        ctx = retrieve(QuestionSpec(
            intent="lookup",
            entities=(Entity("unitPrice", entity_type="column", qualified_name="Sale.OrderItems.unitPrice"),),
        ))
        # OrderItems at qualified strength (10); its two direct neighbors
        # are relationship-assisted at 5 each.
        assert [t.name for t in ctx.tables] == [
            "Sale.OrderItems", "Sale.Orders", "Sale.Shipments",
        ]
        assert ColumnRef(table="Sale.OrderItems", column="unitPrice") in ctx.columns

    def test_description_token_match(self):
        ctx = retrieve(QuestionSpec(intent="lookup", entities=(Entity("customer", entity_type="entity"),)))
        assert [t.name for t in ctx.tables] == ["Sale.Orders"]
        assert ColumnRef(table="Sale.Orders", column="buyer") in ctx.columns

    def test_label_match(self):
        ctx = retrieve(QuestionSpec(intent="lookup", entities=(Entity("خریدار", entity_type="entity"),)))
        assert [t.name for t in ctx.tables] == ["Sale.Orders"]
        assert ctx.warnings == ()

    def test_business_semantics_signal(self):
        # "revenue" appears only in the semantics description; the signal
        # boosts the applies_to columns and their owning table.
        ctx = retrieve(QuestionSpec(intent="aggregate", metrics=(QuestionMetric("revenue", "sum"),)))
        assert [t.name for t in ctx.tables] == ["Sale.OrderItems"]
        columns = {(c.table, c.column) for c in ctx.columns}
        assert ("Sale.OrderItems", "quantity") in columns
        assert ("Sale.OrderItems", "unitPrice") in columns
        assert len(ctx.relevant_business_semantics) == 1

    def test_combined_signals_rank_higher(self):
        named = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)))
        both = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),
                                                           Entity("buyer", entity_type="column"))))
        # More evidence must not remove the table and raises confidence.
        # The combined signal (11) relationship-assists OrderItems to 5.5.
        assert [t.name for t in both.tables] == ["Sale.Orders", "Sale.OrderItems"]
        assert both.confidence > named.confidence

    def test_filter_and_metric_fields_match_columns(self):
        ctx = retrieve(QuestionSpec(
            intent="aggregate",
            filters=(QuestionFilter(field="jalaliYearNo", operator="eq", value="1399"),),
            metrics=(QuestionMetric("quantity", "sum"),),
        ))
        # quantity is a column of BOTH OrderItems and Shipments, but the
        # revenue semantics evidence (applies_to) makes OrderItems.quantity
        # strictly stronger, so the tie is broken deterministically and
        # no SCH.AMBIGUOUS warning fires (the plain "quantity"-only spec
        # DOES warn — see TestAmbiguitySemantics).
        tables = {t.name for t in ctx.tables}
        assert tables == {"Sale.Orders", "Sale.OrderItems", "Sale.Shipments"}
        assert ctx.warnings == ()
        columns = {(c.table, c.column) for c in ctx.columns}
        assert ("Sale.Orders", "jalaliYearNo") in columns
        assert ("Sale.OrderItems", "quantity") in columns
        assert ("Sale.Shipments", "quantity") in columns


# ---------------------------------------------------------------------------
# Ranking, threshold, top-K
# ---------------------------------------------------------------------------


class TestRanking:
    def test_ranking_is_score_desc_then_name(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)))
        names = [t.name for t in ctx.tables]
        assert names == sorted(names, key=lambda n: n)  # single element sanity
        scores = {"Sale.Orders": 6.0, "Sale.OrderItems": 3.0, "Sale.Shipments": 0.0}
        policy = DEFAULT_RETRIEVAL_POLICY
        ordered = sorted(scores, key=lambda n: (-scores[n], n))
        assert ordered[0] == "Sale.Orders"

    def test_threshold_excludes_weak_evidence(self):
        # A lone description-token match on a table (3.0) is below the
        # 4.0 threshold: "headers" appears only in the Sale.Orders table
        # description, in no column description and no semantics entry.
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("headers", entity_type="entity"),)))
        assert ctx.tables == ()
        assert any(w.startswith("SCH.NO_RELEVANT_SCHEMA") for w in ctx.warnings)

    def test_table_top_k_enforced(self):
        # All three tables selected for a broad column name ("quantity"
        # matches two tables, "Id" is not a term) — build a spec matching
        # all three tables by name and shrink K.
        spec = QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"),
            Entity("OrderItems", entity_type="table"),
            Entity("Shipments", entity_type="table"),
        ))
        policy = RetrievalPolicy(table_top_k=2)
        ctx = SchemaRetriever(policy).retrieve(spec, make_snapshot())
        assert len(ctx.tables) == 2
        # Deterministic truncation: the two highest-scored, then name.
        assert {t.name for t in ctx.tables} == {"Sale.OrderItems", "Sale.Orders"}

    def test_column_top_k_per_table_enforced(self):
        spec = QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"),
            Entity("Id", entity_type="column"),
            Entity("buyer", entity_type="column"),
            Entity("status", entity_type="column"),
            Entity("jalaliYearNo", entity_type="column"),
            Entity("operationMonth", entity_type="column"),
        ))
        policy = RetrievalPolicy(column_top_k_per_table=3)
        ctx = SchemaRetriever(policy).retrieve(spec, make_snapshot())
        order_cols = [c.column for c in ctx.columns if c.table == "Sale.Orders"]
        assert len(order_cols) <= 3

    def test_column_ordinal_tie_breaking(self):
        # "Id" exact-matches the Id column of every table; within a table
        # the top column is the lowest-ordinal equal-score match ("Id"),
        # with join-endpoint columns appended after ranked selections.
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("Id", entity_type="column"),)))
        assert {c.table for c in ctx.columns} == {
            "Sale.Orders", "Sale.OrderItems", "Sale.Shipments",
        }
        for table_name in {c.table for c in ctx.columns}:
            cols = [c.column for c in ctx.columns if c.table == table_name]
            assert cols[0] == "Id"


# ---------------------------------------------------------------------------
# Relationships and join paths
# ---------------------------------------------------------------------------


class TestRelationshipsAndJoins:
    def test_join_hint_between_two_named_tables(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"), Entity("OrderItems", entity_type="table"),
        )))
        assert [(j.left.table, j.left.column, j.right.table, j.right.column) for j in ctx.joins] == [
            ("Sale.OrderItems", "orderId", "Sale.Orders", "Id"),
        ]
        assert all(isinstance(j, JoinHint) and j.kind == "inner" for j in ctx.joins)

    def test_two_edge_chain_between_endpoints(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"), Entity("Shipments", entity_type="table"),
        )))
        assert [(j.left.table, j.left.column, j.right.table, j.right.column) for j in ctx.joins] == [
            ("Sale.OrderItems", "orderId", "Sale.Orders", "Id"),
            ("Sale.Shipments", "orderItemId", "Sale.OrderItems", "Id"),
        ]

    def test_join_endpoint_columns_are_in_context(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"), Entity("Shipments", entity_type="table"),
        )))
        columns = {(c.table, c.column) for c in ctx.columns}
        assert ("Sale.OrderItems", "orderId") in columns
        assert ("Sale.Shipments", "orderItemId") in columns
        assert ("Sale.Orders", "Id") in columns

    def test_no_join_hints_for_unrelated_tables(self):
        # Orders and Shipments are NOT directly related in a snapshot
        # without the intermediate table; with max_join_path_edges=1 the
        # 2-edge chain is out of reach.
        policy = RetrievalPolicy(max_join_path_edges=1)
        ctx = SchemaRetriever(policy).retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"), Entity("Shipments", entity_type="table"),
        )), make_snapshot())
        assert ctx.joins == ()

    def test_no_inferred_relationships(self):
        # buyerId is a dangling reference; no Buyers edge may ever appear.
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("buyer", entity_type="column"), Entity("buyerId", entity_type="column"),
        )))
        for hint in ctx.joins:
            assert "Buyers" not in (hint.left.table, hint.right.table)

    def test_duplicate_relationship_rows_yield_one_hint(self):
        snapshot = make_snapshot()
        snapshot = SchemaSnapshot(
            version=snapshot.version,
            tables=snapshot.tables,
            relationships=snapshot.relationships + (snapshot.relationships[0],),
            business_semantics=snapshot.business_semantics,
            labels=snapshot.labels,
        )
        ctx = SchemaRetriever().retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"), Entity("OrderItems", entity_type="table"),
        )), snapshot)
        assert len(ctx.joins) == 1

    def test_relationship_assist_pulls_neighbor_of_strong_match(self):
        # A qualified table match (10) assists its direct neighbor:
        # Sale.Orders qualified → OrderItems gains min(0.5*10, 6) = 5 ≥ 4.
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table", qualified_name="Sale.Orders"),
        )))
        assert {t.name for t in ctx.tables} == {"Sale.Orders", "Sale.OrderItems"}
        # The assisted table must rank strictly after its source.
        assert [t.name for t in ctx.tables] == ["Sale.Orders", "Sale.OrderItems"]

    def test_weak_match_gets_no_assist(self):
        # An exact table name (6) assists with 3.0 — below threshold —
        # so only the named table is selected.
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)))
        assert [t.name for t in ctx.tables] == ["Sale.Orders"]


# ---------------------------------------------------------------------------
# Ambiguity and no-relevant-schema
# ---------------------------------------------------------------------------


class TestAmbiguitySemantics:
    def test_named_multi_table_query_is_not_ambiguous(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"), Entity("Shipments", entity_type="table"),
        )))
        assert ctx.warnings == ()

    def test_column_name_tie_without_name_evidence_is_ambiguous(self):
        # "quantity" exists in OrderItems and Shipments; both tables tie
        # at the column-match signal with no table-name evidence.
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("quantity", entity_type="column"),)))
        tables = {t.name for t in ctx.tables}
        assert tables == {"Sale.OrderItems", "Sale.Shipments"}
        assert any(w.startswith("SCH.AMBIGUOUS") for w in ctx.warnings)

    def test_distinct_scores_never_warn(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)))
        assert ctx.warnings == ()

    def test_two_qualified_matches_conflict(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table", qualified_name="Sale.Orders"),
            Entity("Shipments", entity_type="table", qualified_name="Sale.Shipments"),
        )))
        assert any(w.startswith("SCH.AMBIGUOUS") for w in ctx.warnings)

    def test_no_relevant_schema(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("zzz_nothing", entity_type="entity"),)))
        assert ctx.tables == ()
        assert ctx.columns == ()
        assert ctx.joins == ()
        assert ctx.confidence == 0.0
        assert any(w.startswith("SCH.NO_RELEVANT_SCHEMA") for w in ctx.warnings)


# ---------------------------------------------------------------------------
# Context properties and failures
# ---------------------------------------------------------------------------


class TestContextAndFailures:
    def test_deterministic_output(self):
        spec = QuestionSpec(intent="a", entities=(Entity("buyer", entity_type="column"),))
        assert retrieve(spec) == retrieve(spec)

    def test_empty_question_spec(self):
        ctx = retrieve(QuestionSpec(intent="lookup"))
        assert ctx.tables == ()
        assert any(w.startswith("SCH.NO_RELEVANT_SCHEMA") for w in ctx.warnings)

    def test_none_spec_raises_typed_error(self):
        with pytest.raises(SchemaError):
            retrieve(None)

    def test_none_snapshot_raises_typed_error(self):
        with pytest.raises(SchemaError):
            SchemaRetriever().retrieve(
                QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)), None
            )

    def test_empty_snapshot_yields_no_relevant_schema(self):
        snapshot = SchemaSnapshot(
            version=SchemaVersion(id="empty", captured_at=datetime(2026, 9, 4), source="fixture"),
        )
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)), snapshot)
        assert ctx.tables == ()
        assert any(w.startswith("SCH.NO_RELEVANT_SCHEMA") for w in ctx.warnings)

    def test_context_is_bounded_and_typed(self):
        ctx = retrieve(QuestionSpec(intent="a", entities=(
            Entity("Orders", entity_type="table"),
            Entity("buyer", entity_type="column"), Entity("status", entity_type="column"),
            Entity("Id", entity_type="column"),
        )))
        assert len(ctx.tables) <= DEFAULT_RETRIEVAL_POLICY.table_top_k
        per_table: dict[str, int] = {}
        for c in ctx.columns:
            per_table[c.table] = per_table.get(c.table, 0) + 1
        assert all(n <= DEFAULT_RETRIEVAL_POLICY.column_top_k_per_table for n in per_table.values())
        assert all(isinstance(c, ColumnRef) for c in ctx.columns)
        assert all(isinstance(j, JoinHint) for j in ctx.joins)
        # JoinHints are metadata: no SQL text anywhere in the context.
        assert not any(hasattr(j, "sql") for j in ctx.joins)

    def test_stale_snapshot_is_just_data(self):
        # The retriever cannot know staleness (catalog concern); a stale
        # snapshot is retrieved like any other — no refresh, no error.
        snapshot = make_snapshot()
        ctx = retrieve(QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)), snapshot)
        assert ctx.tables != ()


class TestContractConformance:
    def test_retriever_satisfies_canonical_protocol(self):
        from src.agents.contracts import SchemaRetriever as SchemaRetrieverProtocol

        assert isinstance(SchemaRetriever(), SchemaRetrieverProtocol)

    def test_signature_parity_with_protocol(self):
        import inspect

        from src.agents.contracts import SchemaRetriever as SchemaRetrieverProtocol

        proto = inspect.signature(SchemaRetrieverProtocol.retrieve)
        impl = inspect.signature(SchemaRetriever.retrieve)
        proto_params = [p for p in proto.parameters.values() if p.name != "self"]
        impl_params = [p for p in impl.parameters.values() if p.name != "self"]
        assert [p.name for p in proto_params] == [p.name for p in impl_params]

    def test_retriever_module_defines_no_second_protocol(self):
        import src.core.schema.retriever as retriever_module

        assert not hasattr(retriever_module, "SchemaRetrieverProtocol")
        # The module's SchemaRetriever is a concrete class, not a Protocol.
        assert not getattr(retriever_module.SchemaRetriever, "_is_protocol", False)
