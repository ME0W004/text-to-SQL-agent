# src/core/schema/renderer.py

"""SchemaContextRenderer — the controlled LLM-facing schema rendering
boundary (Phase 3.2).

    SchemaContext  ->  SchemaContextRenderer.render()  ->  LLM schema payload

The renderer is **formatting, not retrieval**: it owns the
representation of a request-scoped, already-bounded
:class:`SchemaContext` for downstream LLM-facing consumers. It never
replaces the retriever's selection authority and never widens the
context.

Obligations (Phase 13 of the approved design):

* accepts a ``SchemaContext`` — never raw database metadata, never a
  ``SchemaSnapshot`` (typed rejection otherwise);
* never queries the database, never accesses the ``SchemaCatalog``,
  never touches a QuestionAnalyzer and never calls the ``LLMRouter``
  (the module imports the contract value objects and nothing else);
* never invents columns, tables or relationships — every rendered
  element comes verbatim from the given context;
* preserves relationship direction/provenance (the hint's canonical
  ``left``/``right`` order is rendered unchanged);
* is deterministic: the same context always renders to the same text;
* enforces a hard size limit: when the full rendering exceeds
  ``max_chars``, lower-priority sections are dropped in a documented
  order (semantics first, then notes, then per-table columns beyond
  the join endpoints); if even the structural floor (tables, joins,
  endpoint columns) cannot fit, rendering fails with a typed
  ``SchemaError`` — fail-closed, never silently unbounded.

Until the separately-authorized prompt-migration stage wires it in,
the renderer is dormant infrastructure (like the retriever before
3.1C): the live SQL-generation prompt still embeds the legacy
hand-authored full-schema text (documented transitional dual-schema
path; retirement of that legacy source requires measured retrieval
parity plus explicit authorization — see
``docs/architecture/schema-intelligence.md`` §20).
"""

from __future__ import annotations

from src.agents.contracts import SchemaContext, SchemaError

__all__ = ["SchemaContextRenderer", "DEFAULT_MAX_RENDER_CHARS"]


DEFAULT_MAX_RENDER_CHARS = 8000


class SchemaContextRenderer:
    """Deterministic, bounded renderer of a :class:`SchemaContext`."""

    def __init__(self, max_chars: int = DEFAULT_MAX_RENDER_CHARS) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be a positive integer.")
        self._max_chars = max_chars

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def render(self, context: SchemaContext) -> str:
        """Render the context to deterministic bounded text."""
        if not isinstance(context, SchemaContext):
            raise SchemaError(
                "SchemaContextRenderer requires a SchemaContext.",
                code="SCH.INTERNAL",
            )
        endpoints = self._endpoint_keys(context)
        # Section-drop priority (documented): full render; without the
        # semantics section; without semantics/notes and with per-table
        # columns reduced to join endpoints; structural floor.
        attempts = (
            {"include_semantics": True, "include_notes": True, "endpoint_columns_only": False},
            {"include_semantics": False, "include_notes": True, "endpoint_columns_only": False},
            {"include_semantics": False, "include_notes": False, "endpoint_columns_only": True},
        )
        text = ""
        for options in attempts:
            text = self._render(context, endpoints=endpoints, **options)
            if len(text) <= self._max_chars:
                return text
        raise SchemaError(
            "Rendered schema payload exceeds the hard render limit even at the "
            f"structural floor ({len(text)} chars > {self._max_chars}); "
            "refusing to render unbounded schema text.",
            code="SCH.INTERNAL",
        )

    # ------------------------------------------------------------------
    # Rendering (pure formatting — context fields only, in context order)
    # ------------------------------------------------------------------

    def _render(
        self,
        context: SchemaContext,
        *,
        endpoints: set[tuple[str, str]],
        include_semantics: bool,
        include_notes: bool,
        endpoint_columns_only: bool,
    ) -> str:
        lines = ["[SCHEMA CONTEXT]"]
        if context.tables:
            lines.append("Tables: " + "; ".join(t.name for t in context.tables))
        else:
            lines.append("Tables: (none)")
        columns_by_table: dict[str, list[str]] = {}
        for ref in context.columns:
            if endpoint_columns_only and (ref.table, ref.column) not in endpoints:
                continue
            columns_by_table.setdefault(ref.table, []).append(ref.column)
        if columns_by_table:
            lines.append("Columns:")
            # Selected-table order first, then any residual column table
            # in first-appearance order (defensive; the retriever never
            # emits columns outside selected tables).
            table_order = [t.name for t in context.tables] + [
                name for name in columns_by_table if name not in {t.name for t in context.tables}
            ]
            for table in table_order:
                cols = columns_by_table.get(table)
                if cols:
                    lines.append(f"- {table}: {', '.join(cols)}")
        if context.joins:
            lines.append("Joins:")
            for hint in context.joins:
                lines.append(
                    f"- {hint.left.table}.{hint.left.column} = "
                    f"{hint.right.table}.{hint.right.column}"
                )
        if include_semantics and context.relevant_business_semantics:
            lines.append("Semantics:")
            for s in context.relevant_business_semantics:
                entry = s.description
                if s.formula:
                    entry = f"{entry} [formula: {s.formula}]"
                lines.append(f"- {entry}")
        if include_notes and context.warnings:
            lines.append("Notes:")
            for warning in context.warnings:
                lines.append(f"- {warning}")
        return "\n".join(lines)

    @staticmethod
    def _endpoint_keys(context: SchemaContext) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for hint in context.joins:
            keys.add((hint.left.table, hint.left.column))
            keys.add((hint.right.table, hint.right.column))
        return keys
