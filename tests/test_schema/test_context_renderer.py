# tests/test_schema/test_context_renderer.py

"""SchemaContextRenderer tests (Phase 3.2, rendering boundary).

The renderer is formatting, not retrieval: these tests pin the
determinism, the context-only input contract, the no-invention rule,
the direction preservation and the hard size limit with its documented
section-drop priority.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.agents.contracts import (
    BusinessSemantics,
    ColumnRef,
    JoinHint,
    SchemaContext,
    SchemaError,
    SchemaSnapshot,
    SchemaVersion,
    TableDescriptor,
)
from src.core.schema.renderer import DEFAULT_MAX_RENDER_CHARS, SchemaContextRenderer


def make_context() -> SchemaContext:
    return SchemaContext(
        tables=(
            _TableRef("Sale.Orders"),
            _TableRef("Sale.OrderItems"),
        ),
        columns=(
            ColumnRef(table="Sale.OrderItems", column="quantity"),
            ColumnRef(table="Sale.OrderItems", column="unitPrice"),
            ColumnRef(table="Sale.Orders", column="buyer"),
        ),
        joins=(
            JoinHint(
                left=ColumnRef(table="Sale.OrderItems", column="orderId"),
                right=ColumnRef(table="Sale.Orders", column="Id"),
            ),
        ),
        relevant_business_semantics=(
            BusinessSemantics(
                description="Revenue for a line item is computed as quantity times unitPrice",
                formula="quantity * unitPrice",
                applies_to=("Sale.OrderItems.quantity", "Sale.OrderItems.unitPrice"),
            ),
        ),
        confidence=1.0,
        warnings=("SCH.AMBIGUOUS: table candidates are materially tied",),
    )


def _TableRef(name: str):
    from src.agents.contracts import TableRef

    return TableRef(name=name)


class TestRendering:
    def test_renders_all_sections_in_context_order(self):
        text = SchemaContextRenderer().render(make_context())
        lines = text.splitlines()
        assert lines[0] == "[SCHEMA CONTEXT]"
        assert "Tables: Sale.Orders; Sale.OrderItems" in lines
        # Column grouping follows selected-table order.
        join_idx = next(i for i, l in enumerate(lines) if l == "Joins:")
        columns_text = "\n".join(lines[:join_idx])
        orders_pos = columns_text.index("Sale.Orders:")
        orderitems_pos = columns_text.index("Sale.OrderItems:")
        assert orders_pos < orderitems_pos
        assert "- Sale.OrderItems: quantity, unitPrice" in lines
        assert "- Sale.OrderItems.orderId = Sale.Orders.Id" in lines
        assert "- Revenue for a line item is computed as quantity times unitPrice [formula: quantity * unitPrice]" in lines
        assert "- SCH.AMBIGUOUS: table candidates are materially tied" in lines

    def test_deterministic(self):
        renderer = SchemaContextRenderer()
        assert renderer.render(make_context()) == renderer.render(make_context())

    def test_empty_context_renders_placeholder_tables(self):
        text = SchemaContextRenderer().render(SchemaContext())
        assert "Tables: (none)" in text

    def test_direction_preserved_verbatim(self):
        # The hint's canonical left/right order is rendered unchanged
        # (left = right), never swapped or rewritten.
        context = SchemaContext(
            tables=(_TableRef("A"), _TableRef("B")),
            joins=(
                JoinHint(
                    left=ColumnRef(table="B", column="a_id"),
                    right=ColumnRef(table="A", column="Id"),
                ),
            ),
        )
        assert "- B.a_id = A.Id" in SchemaContextRenderer().render(context)


class TestInputContract:
    def test_rejects_none(self):
        with pytest.raises(SchemaError):
            SchemaContextRenderer().render(None)  # type: ignore[arg-type]

    def test_rejects_snapshot(self):
        snapshot = SchemaSnapshot(
            version=SchemaVersion(id="s", captured_at=datetime(2026, 9, 4), source="t"),
            tables=(TableDescriptor(name="Sale.SecretTable", columns=("Id",)),),
        )
        with pytest.raises(SchemaError):
            SchemaContextRenderer().render(snapshot)  # type: ignore[arg-type]

    def test_never_renders_content_outside_the_context(self):
        # A snapshot may contain far more than the context: the rendered
        # payload must mention nothing beyond the context's selection.
        snapshot_tables = ("Sale.Orders", "Sale.OrderItems", "Sale.Shipments", "Sale.AuditTrail")
        context = make_context()
        text = SchemaContextRenderer().render(context)
        for name in snapshot_tables:
            if name not in {t.name for t in context.tables}:
                assert name not in text


class TestHardSizeLimit:
    def test_default_limit_never_binds_for_normal_contexts(self):
        text = SchemaContextRenderer().render(make_context())
        assert len(text) <= DEFAULT_MAX_RENDER_CHARS

    def test_semantics_dropped_first(self):
        full = SchemaContextRenderer().render(make_context())
        no_semantics = SchemaContextRenderer(max_chars=len(full) - 1).render(make_context())
        assert "Semantics:" not in no_semantics
        assert "Tables: Sale.Orders; Sale.OrderItems" in no_semantics
        assert "Notes:" in no_semantics

    def test_notes_dropped_next(self):
        full = SchemaContextRenderer().render(make_context())
        without_semantics_len = len(
            SchemaContextRenderer(max_chars=len(full) - 1).render(make_context())
        )
        tighter = SchemaContextRenderer(max_chars=without_semantics_len - 1).render(make_context())
        assert "Semantics:" not in tighter
        assert "Notes:" not in tighter
        # Joins survive (never dropped).
        assert "- Sale.OrderItems.orderId = Sale.Orders.Id" in tighter

    def test_endpoint_columns_fallback_then_typed_failure(self):
        # A limit below the structural floor (tables + joins + endpoints)
        # fails closed with a typed error — never silently unbounded.
        with pytest.raises(SchemaError):
            SchemaContextRenderer(max_chars=40).render(make_context())

    def test_endpoint_columns_survive_truncation(self):
        context = SchemaContext(
            tables=(_TableRef("A"), _TableRef("B")),
            columns=(
                ColumnRef(table="A", column="c1"),
                ColumnRef(table="A", column="c2"),
                ColumnRef(table="A", column="c3"),
                ColumnRef(table="B", column="a_id"),
            ),
            joins=(
                JoinHint(
                    left=ColumnRef(table="B", column="a_id"),
                    right=ColumnRef(table="A", column="c2"),
                ),
            ),
        )
        # Small enough to force the endpoint-only fallback tier
        # (full render is 87 chars; endpoint-only render is ~74).
        text = SchemaContextRenderer(max_chars=80).render(context)
        assert "A.c2" in text
        assert "B: a_id" in text
        assert "c1" not in text and "c3" not in text

    def test_deterministic_under_limit(self):
        renderer = SchemaContextRenderer(max_chars=200)
        assert renderer.render(make_context()) == renderer.render(make_context())

    def test_invalid_limit_rejected(self):
        with pytest.raises(ValueError):
            SchemaContextRenderer(max_chars=0)
