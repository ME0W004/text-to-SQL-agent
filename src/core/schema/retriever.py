# src/core/schema/retriever.py

"""Deterministic SchemaRetriever (ADR-002/ADR-017, Sprint 3.1C).

Selects the schema subset allowed for a request:

    QuestionSpec + SchemaSnapshot  ->  SchemaContext

Pure, deterministic, bounded, explainable. The retriever:

* consumes an already-built snapshot (never refreshes, never touches a
  database, catalog refresh, LLM, prompt, memory, or AgentState);
* scores tables and columns against the QuestionSpec's existing fields
  only (entities, filters, metrics — no natural-language parsing);
* expands table relevance along relationships already present in the
  snapshot (never inferred);
* emits ``JoinHint`` metadata for known relationship paths — hints are
  planning metadata, never executable SQL;
* reports ``SCH.NO_RELEVANT_SCHEMA`` / ``SCH.AMBIGUOUS`` as context
  warnings per the component contract (raised only if an orchestrator
  policy demands hard rejection, which is not implemented here).

Scoring model (centralized in :class:`RetrievalPolicy`, documented in
``docs/architecture/schema-intelligence.md`` §19):

* per-table additive signals — qualified-name exact (10; also fired by a
  qualified column reference naming the table), exact unqualified-name
  (6), case-insensitive name (5, only when exact did not fire), Persian
  label equality (4), description-token overlap (3; suppressed when the
  table was explicitly named — the name is the same evidence);
* per-column additive signals — qualified exact (10), name exact (6),
  case-insensitive (5), label equality (4), description-token (3),
  business-semantics token (3);
* a table owning any column whose score reaches ``min_column_score``
  gains ``w_column_match_table`` (5) — column/label/semantics evidence
  makes the owning table relevant;
* one relationship-assist pass: a table below threshold gains
  ``min(factor × strongest_neighbor_score, cap)`` from its
  snapshot neighbors (factor 0.5, cap 6) — never iterated, so no
  feedback loops; only qualified-strength neighbors (≥ ``w_qualified_table``
  = 10) pull a zero-evidence neighbor past the threshold, and assisted
  tables always rank strictly below their source (ordinary multi-table
  relevance cannot become ambiguity);
* selection: tables ≥ ``min_table_score`` sorted by (score desc, name),
  truncated to ``table_top_k``; columns ≥ ``min_column_score`` per
  selected table sorted by (score desc, ordinal, name), truncated to
  ``column_top_k_per_table``; join-hint endpoint columns are always
  included.

Phase 3.2 hardening (production retrieval boundary):

* **Query-type-aware evidence routing** — QuestionSpec fields are not
  equivalent evidence: filter fields are *column-scoped* (they never
  count as table-name or table-description evidence), while untyped
  entities and metric names are *dual-surface* (a metric name may
  legitimately appear in a table description or business semantics).
  ``time_constraints``/``references`` carry no demonstrated
  deterministic mapping yet and contribute nothing (documented
  QuestionAnalyzer prerequisite).
* **Hard context bounds** — beyond the top-K limits the policy caps
  join hints, semantic entries and the canonical serialized context
  size (``max_context_chars``). The bound exists before the LLM
  boundary; no token estimation is performed (no estimator exists in
  the repository and provider-specific token semantics must not be
  invented).
* **Deterministic context budgeting** — when the serialized size
  exceeds the budget, lowest-value optional metadata is removed in a
  documented priority order (semantics in reverse canonical order,
  then non-endpoint columns lowest-score-first). Tables, join hints,
  join-endpoint columns and warnings are never removed; trimming is
  reported with a ``SCH.CONTEXT_BOUNDED`` warning, never hidden.
* **Telemetry** — one ``schema_retrieval_completed`` event per
  retrieval (the component-contract §3 surface): counts, confidence,
  context size, snapshot version and warning *codes* only. The user
  question, prompt text, schema text and row data are never logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter

from src.agents.contracts import (
    ColumnRef,
    JoinHint,
    QuestionSpec,
    SchemaContext,
    SchemaError,
    SchemaSnapshot,
    TableRef,
)
from src.utils.logger import log_event

__all__ = [
    "SchemaRetriever",
    "RetrievalPolicy",
    "DEFAULT_RETRIEVAL_POLICY",
    "serialized_context_size",
]


@dataclass(frozen=True)
class RetrievalPolicy:
    """Centralized, documented retrieval limits and scoring weights."""

    # Selection limits
    min_table_score: float = 4.0
    min_column_score: float = 3.0
    table_top_k: int = 3
    column_top_k_per_table: int = 6
    max_join_path_edges: int = 2
    # Hard context bounds (Phase 3.2). The serialized-size bound exists
    # before the LLM boundary — it never relies on prompt limits. The
    # defaults are far above any context the default top-K limits can
    # produce (3 tables × 6 columns + ≤ 6 hints on this catalog), so
    # they only bind on reduced policies or oversized annotations.
    # No token estimate is computed: the repository has no token
    # estimator and provider-specific token semantics must not be
    # invented (character size is the honest, provider-neutral bound).
    max_join_hints: int = 12
    max_semantic_entries: int = 8
    max_context_chars: int = 8000
    # Table signal weights
    w_qualified_table: float = 10.0
    w_table_name_exact: float = 6.0
    w_name_case_insensitive: float = 5.0
    w_label_exact: float = 4.0
    w_description_token: float = 3.0
    # A table that owns a matched column (any column signal reaching
    # min_column_score: name, label, description or semantics evidence)
    # becomes relevant too — a question about "buyer" or «خریدار» or
    # "revenue" must surface the owning table even when no table-name
    # signal fires. A qualified column reference selects its table at
    # full qualified strength.
    w_column_match_table: float = 5.0
    # Column signal weights (w_qualified/w_name/w_label/w_description shared).
    # w_semantics_token equals the description weight: both are weak
    # textual evidence (3.0 reaches min_column_score alone, which is the
    # documented behavior — a "revenue" question selects the columns its
    # business semantics point at).
    w_qualified_column: float = 10.0
    w_column_name_exact: float = 6.0
    w_semantics_token: float = 3.0
    # Relationship assist
    relationship_assist_factor: float = 0.5
    max_relationship_assist: float = 6.0


DEFAULT_RETRIEVAL_POLICY = RetrievalPolicy()

_SCORE_EPSILON = 1e-9

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str | None) -> frozenset[str]:
    """Lower-cased word tokens (Unicode-aware: Persian included)."""
    if not text:
        return frozenset()
    return frozenset(token.casefold() for token in _TOKEN_RE.findall(text))


def _unqualified(qualified_name: str) -> str:
    return qualified_name.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class _Terms:
    """Query terms routed by QuestionSpec field type (Phase 3.2).

    Evidence is not interchangeable:

    * ``table_only``/``column_only`` hold entity texts typed explicitly
      as "table"/"column";
    * ``general`` holds untyped entity texts — dual-surface evidence;
    * ``metrics`` holds metric names and "metric"-typed entities — also
      dual-surface, because a metric is a business *concept* that may
      legitimately surface in table descriptions and semantics;
    * ``filters`` holds filter fields — **column-scoped only**: a filter
      constrains a field and is never evidence that a table was named
      or described;
    * ``qualified`` holds explicit qualified names (matched against
      qualified table/column surfaces at full strength).

    ``time_constraints``/``references`` have no demonstrated
    deterministic mapping yet and contribute nothing (documented
    QuestionAnalyzer prerequisite, not silently ignored).
    """

    table_only: frozenset[str]
    column_only: frozenset[str]
    general: frozenset[str]
    metrics: frozenset[str]
    filters: frozenset[str]
    qualified: frozenset[str]

    def is_empty(self) -> bool:
        return not (
            self.table_only
            or self.column_only
            or self.general
            or self.metrics
            or self.filters
            or self.qualified
        )

    def for_tables(self) -> frozenset[str]:
        # Entity mentions and metric concepts may name a table; filter
        # fields may not (column-scoped evidence).
        return self.table_only | self.general | self.metrics

    def for_columns(self) -> frozenset[str]:
        return self.column_only | self.general | self.metrics | self.filters


def _collect_terms(spec: QuestionSpec) -> _Terms:
    table_only: set[str] = set()
    column_only: set[str] = set()
    general: set[str] = set()
    metrics: set[str] = set()
    filters: set[str] = set()
    qualified: set[str] = set()
    for entity in spec.entities:
        if entity.qualified_name:
            qualified.add(entity.qualified_name)
        if not entity.text:
            continue
        if entity.entity_type == "table":
            table_only.add(entity.text)
        elif entity.entity_type == "column":
            column_only.add(entity.text)
        elif entity.entity_type == "metric":
            metrics.add(entity.text)
        else:
            general.add(entity.text)
    for f in spec.filters:
        if f.field:
            filters.add(f.field)
    for m in spec.metrics:
        if m.name:
            metrics.add(m.name)
    return _Terms(
        frozenset(table_only),
        frozenset(column_only),
        frozenset(general),
        frozenset(metrics),
        frozenset(filters),
        frozenset(qualified),
    )


def serialized_context_size(context: SchemaContext) -> int:
    """Canonical serialized size of a :class:`SchemaContext` (characters).

    The single size measure used by the retriever's budget, the
    ``SCH.CONTEXT_BOUNDED`` reporting and the Orchestrator's runtime
    metadata. Canonical JSON (sorted keys, compact separators,
    ``ensure_ascii=False``) mirrors the catalog's serialization
    convention, so the same context always measures the same size.
    """
    payload = {
        "tables": [{"name": t.name, "alias": t.alias} for t in context.tables],
        "columns": [{"table": c.table, "column": c.column} for c in context.columns],
        "joins": [
            {
                "left": {"table": j.left.table, "column": j.left.column},
                "right": {"table": j.right.table, "column": j.right.column},
                "kind": j.kind,
            }
            for j in context.joins
        ],
        "relevant_business_semantics": [
            {
                "description": s.description,
                "formula": s.formula,
                "applies_to": list(s.applies_to),
            }
            for s in context.relevant_business_semantics
        ],
        "confidence": context.confidence,
        "warnings": list(context.warnings),
    }
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class SchemaRetriever:
    """Pure deterministic selection of a :class:`SchemaContext`."""

    def __init__(self, policy: RetrievalPolicy = DEFAULT_RETRIEVAL_POLICY) -> None:
        self._policy = policy

    # ------------------------------------------------------------------
    # Public surface (canonical SchemaRetriever Protocol)
    # ------------------------------------------------------------------

    def retrieve(self, spec: QuestionSpec, snapshot: SchemaSnapshot) -> SchemaContext:
        if spec is None or snapshot is None:
            raise SchemaError(
                "SchemaRetriever requires a QuestionSpec and a SchemaSnapshot.",
                code="SCH.INTERNAL",
            )
        started = perf_counter()
        context = self._retrieve_bounded(spec, snapshot)
        self._log_completed(context, snapshot, (perf_counter() - started) * 1000.0)
        return context

    def _retrieve_bounded(self, spec: QuestionSpec, snapshot: SchemaSnapshot) -> SchemaContext:
        terms = _collect_terms(spec)
        if terms.is_empty():
            return SchemaContext(
                confidence=0.0,
                warnings=(
                    "SCH.NO_RELEVANT_SCHEMA: QuestionSpec carries no retrieval signals "
                    "(no entities, filters or metrics).",
                ),
            )

        column_scores = self._score_all_columns(snapshot, terms)
        table_scores, name_signaled = self._score_tables(snapshot, terms, column_scores)
        self._apply_relationship_assist(snapshot, table_scores)
        selected = self._select_tables(table_scores)
        warnings = list(self._ambiguity_warnings(table_scores, name_signaled, terms, selected))

        if not selected:
            return SchemaContext(
                confidence=0.0,
                warnings=tuple(warnings)
                + (
                    "SCH.NO_RELEVANT_SCHEMA: no schema object reached the relevance "
                    f"threshold ({self._policy.min_table_score}).",
                ),
            )

        columns = self._select_columns(snapshot, column_scores, selected)
        joins = self._join_hints(snapshot, selected)
        # Join endpoints are planning prerequisites — always in context.
        context_columns = list(columns)
        known = {(c.table, c.column) for c in context_columns}
        for hint in joins:
            for ref in (hint.left, hint.right):
                if (ref.table, ref.column) not in known:
                    context_columns.append(ref)
                    known.add((ref.table, ref.column))

        # Semantics whose applies_to references a selected table
        # (table-level, not column-top-K — trimming columns must not
        # silently drop grounding context).
        selected_prefixes = tuple(f"{table}." for table in selected)
        semantics = tuple(
            s
            for s in snapshot.business_semantics
            if any(applies.startswith(prefix) for applies in s.applies_to for prefix in selected_prefixes)
        )

        best = max(table_scores[t] for t in selected)
        confidence = round(min(1.0, best / 10.0), 4)
        return self._bound_context(
            tables=tuple(TableRef(name=t) for t in selected),
            columns=context_columns,
            joins=joins,
            semantics=semantics,
            warnings=warnings,
            confidence=confidence,
            column_scores=column_scores,
            snapshot=snapshot,
        )

    # ------------------------------------------------------------------
    # Context bounds and deterministic budgeting (Phase 3.2)
    # ------------------------------------------------------------------

    def _bound_context(
        self,
        *,
        tables: tuple[TableRef, ...],
        columns: list[ColumnRef],
        joins: tuple[JoinHint, ...],
        semantics: tuple,
        warnings: list[str],
        confidence: float,
        column_scores: dict[str, dict[str, float]],
        snapshot: SchemaSnapshot,
    ) -> SchemaContext:
        """Apply the hard policy bounds; report any trimming explicitly.

        Priority (documented, deterministic — never random truncation):

        * never removed: selected tables, join hints, join-endpoint
          columns, warnings (a relationship is never truncated while
          leaving one endpoint unexplained, and a hint is never split
          from its endpoint columns);
        * removed first: semantic entries beyond ``max_semantic_entries``
          (canonical snapshot order kept from the front), then — only
          while the serialized size exceeds ``max_context_chars`` —
          remaining semantics in reverse canonical order, then
          non-endpoint columns lowest-score-first (ties: lexicographically
          first table, then highest ordinal, then lexicographically first
          column name);
        * if the structural floor (tables + hints + endpoints + warnings)
          still exceeds the budget, the floor is kept and the breach is
          reported — never silently unbounded.
        """
        policy = self._policy
        bounded = False
        dropped_semantics = 0
        dropped_columns = 0
        joins = tuple(joins)

        if len(joins) > policy.max_join_hints:
            # Hints arrive canonically sorted; the canonically-first
            # hints are kept. Defaults never bind for table_top_k ≤ 3.
            joins = tuple(joins[: policy.max_join_hints])
            bounded = True

        if len(semantics) > policy.max_semantic_entries:
            semantics = tuple(semantics[: policy.max_semantic_entries])
            bounded = True

        def _build(sems: tuple, cols: list[ColumnRef], extra_warning: str | None) -> SchemaContext:
            trial_warnings = tuple(warnings + ([extra_warning] if extra_warning else []))
            return SchemaContext(
                tables=tables,
                columns=tuple(cols),
                joins=joins,
                relevant_business_semantics=tuple(sems),
                confidence=confidence,
                warnings=trial_warnings,
            )

        sem_list = list(semantics)
        col_list = list(columns)
        endpoint_keys = {
            (ref.table, ref.column) for hint in joins for ref in (hint.left, hint.right)
        }
        ordinals = {
            (table.name, name): index
            for table in snapshot.tables
            for index, name in enumerate(table.columns)
        }
        # Drop order for non-endpoint columns (documented priority):
        # lowest score first; ties → lexicographically first table, then
        # highest ordinal, then lexicographically first column name.
        droppable = sorted(
            (ref for ref in col_list if (ref.table, ref.column) not in endpoint_keys),
            key=lambda ref: (
                column_scores.get(ref.table, {}).get(ref.column, 0.0),
                ref.table,
                -ordinals.get((ref.table, ref.column), 0),
                ref.column,
            ),
        )

        def _notice(content_size: int, floor: bool) -> str:
            if floor:
                return (
                    "SCH.CONTEXT_BOUNDED: structural context floor "
                    f"({content_size} chars) exceeds the configured budget "
                    f"({policy.max_context_chars}); required evidence "
                    "(tables, join hints, endpoint columns) retained."
                )
            return (
                "SCH.CONTEXT_BOUNDED: schema context was trimmed to the "
                f"retrieval budget (dropped {dropped_semantics} semantics "
                f"entries and {dropped_columns} columns; content size "
                f"{content_size} chars, budget {policy.max_context_chars})."
            )

        # The budgeted quantity is the context size *without* the
        # bounded-context notice (the notice is produced by the bound
        # itself and reports exactly this content size). The trimming
        # loop measures the full candidate — notice included — so the
        # delivered context respects the budget whenever the structural
        # floor allows it.
        content_size = serialized_context_size(_build(tuple(sem_list), col_list, None))
        size_bounded = content_size > policy.max_context_chars
        if size_bounded:
            bounded = True
            while True:
                candidate = _notice(content_size, floor=False)
                total = serialized_context_size(
                    _build(tuple(sem_list), col_list, candidate)
                )
                if total <= policy.max_context_chars:
                    break
                if not sem_list and not droppable:
                    # Structural floor reached and still over budget:
                    # required evidence is retained, breach reported.
                    candidate = _notice(content_size, floor=True)
                    break
                if sem_list:
                    sem_list.pop()
                    dropped_semantics += 1
                else:
                    col_list.remove(droppable.pop(0))
                    dropped_columns += 1
                content_size = serialized_context_size(_build(tuple(sem_list), col_list, None))
            warnings.append(candidate)
        elif bounded:
            warnings.append(
                "SCH.CONTEXT_BOUNDED: a retrieval limit trimmed the context "
                f"(content size {content_size} chars, budget "
                f"{policy.max_context_chars})."
            )

        return SchemaContext(
            tables=tables,
            columns=tuple(col_list),
            joins=joins,
            relevant_business_semantics=tuple(sem_list),
            confidence=confidence,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------
    # Telemetry (component-contracts §3: one retrieval event)
    # ------------------------------------------------------------------

    def _log_completed(self, context: SchemaContext, snapshot: SchemaSnapshot, duration_ms: float) -> None:
        """Emit ``schema_retrieval_completed`` — counts, sizes and warning
        *codes* only. Never the question, prompts, schema text or row data."""
        codes = sorted({w.split(":", 1)[0] for w in context.warnings})
        if any(code == "SCH.NO_RELEVANT_SCHEMA" for code in codes):
            outcome = "no_match"
        elif any(code == "SCH.AMBIGUOUS" for code in codes):
            outcome = "ambiguous"
        else:
            outcome = "completed"
        log_event(
            "schema_retrieval_completed",
            component="schema_retriever",
            outcome=outcome,
            selected_tables=len(context.tables),
            selected_columns=len(context.columns),
            join_hints=len(context.joins),
            semantics_entries=len(context.relevant_business_semantics),
            context_chars=serialized_context_size(context),
            confidence=context.confidence,
            bounded="SCH.CONTEXT_BOUNDED" in codes,
            snapshot_version=snapshot.version.id,
            warning_codes=codes,
            duration_ms=round(duration_ms, 3),
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_tables(
        self,
        snapshot: SchemaSnapshot,
        terms: _Terms,
        column_scores: dict[str, dict[str, float]],
    ) -> tuple[dict[str, float], set[str]]:
        """Returns ({table: score}, {tables with an explicit name signal}).

        A *name signal* is a qualified-name match, an exact unqualified
        match or a case-insensitive name match — evidence that the
        question explicitly named the table. Generic signals (labels,
        descriptions, column matches) are not name evidence.
        """
        policy = self._policy
        table_terms = terms.for_tables()
        scores: dict[str, float] = {}
        name_signaled: set[str] = set()
        for table in snapshot.tables:
            unqualified = _unqualified(table.name)
            score = 0.0
            if table.name in terms.qualified:
                score += policy.w_qualified_table
                name_signaled.add(table.name)
            elif any(
                q.startswith(table.name + ".") for q in terms.qualified
            ):
                # A qualified column reference names its table.
                score += policy.w_qualified_table
                name_signaled.add(table.name)
            if any(t == unqualified for t in table_terms):
                score += policy.w_table_name_exact
                name_signaled.add(table.name)
            elif any(t.casefold() == unqualified.casefold() for t in table_terms):
                score += policy.w_name_case_insensitive
                name_signaled.add(table.name)
            description_tokens = _tokens(table.description)
            if (
                description_tokens
                and table.name not in name_signaled
                and any(_tokens(t) & description_tokens for t in table_terms)
            ):
                # Suppressed when the table was explicitly named: the
                # description overlap with the table's own name is the
                # same evidence and must not double-count.
                score += policy.w_description_token
            prefix = table.name + "."
            label_values = {
                value for key, value in snapshot.labels.items() if key.startswith(prefix)
            }
            if any(t in label_values for t in table_terms):
                score += policy.w_label_exact
            # A table owning matched columns becomes relevant.
            table_column_scores = column_scores.get(table.name, {})
            if any(
                s >= policy.min_column_score for s in table_column_scores.values()
            ):
                score += policy.w_column_match_table
            scores[table.name] = score
        return scores, name_signaled

    def _apply_relationship_assist(self, snapshot: SchemaSnapshot, table_scores: dict[str, float]) -> None:
        """Single assist pass from qualified-strength neighbors only.

        A table below threshold gains ``min(0.5 × neighbor_score, 6)``
        only when its strongest snapshot neighbor reached
        ``w_qualified_table`` (10) — i.e., the question explicitly named
        the neighbor. Weak textual evidence never cascades across the
        join graph, and assisted tables always rank strictly below their
        source.
        """
        policy = self._policy
        neighbors: dict[str, set[str]] = {}
        for rel in snapshot.relationships:
            neighbors.setdefault(rel.from_table, set()).add(rel.to_table)
            neighbors.setdefault(rel.to_table, set()).add(rel.from_table)
        assists: dict[str, float] = {}
        for name, score in table_scores.items():
            if score >= policy.min_table_score:
                continue
            strongest = max(
                (table_scores.get(n, 0.0) for n in neighbors.get(name, ())),
                default=0.0,
            )
            if strongest >= policy.w_qualified_table:
                assists[name] = min(
                    policy.relationship_assist_factor * strongest,
                    policy.max_relationship_assist,
                )
        for name, assist in assists.items():
            table_scores[name] += assist

    def _select_tables(self, table_scores: dict[str, float]) -> tuple[str, ...]:
        policy = self._policy
        qualified_names = sorted(
            (name for name, score in table_scores.items() if score >= policy.min_table_score),
            key=lambda name: (-table_scores[name], name),
        )
        return tuple(qualified_names[: policy.table_top_k])

    def _score_columns(self, table, snapshot: SchemaSnapshot, terms: _Terms) -> dict[str, float]:
        """Score one table's columns; returns {column_name: score}."""
        policy = self._policy
        column_terms = terms.for_columns()
        qualified_prefix = table.name + "."
        # Semantics entries whose description/formula matched a term token.
        matched_semantics: list[frozenset[str]] = []
        for s in snapshot.business_semantics:
            signal = _tokens(s.description) | _tokens(s.formula)
            if signal and any(_tokens(t) & signal for t in column_terms):
                matched_semantics.append(frozenset(s.applies_to))
        scores: dict[str, float] = {}
        label_by_column = {
            key.rsplit(".", 1)[-1]: value
            for key, value in snapshot.labels.items()
            if key.startswith(qualified_prefix)
        }
        for detail in table.column_details:
            score = 0.0
            if f"{table.name}.{detail.name}" in terms.qualified:
                score += policy.w_qualified_column
            if any(t == detail.name for t in column_terms):
                score += policy.w_column_name_exact
            elif any(t.casefold() == detail.name.casefold() for t in column_terms):
                score += policy.w_name_case_insensitive
            if detail.name in label_by_column and any(
                t == label_by_column[detail.name] for t in column_terms
            ):
                score += policy.w_label_exact
            description_tokens = _tokens(detail.description)
            if description_tokens and any(_tokens(t) & description_tokens for t in column_terms):
                score += policy.w_description_token
            qualified_name = f"{table.name}.{detail.name}"
            if any(
                qualified_name in applies or detail.name in applies
                for applies in matched_semantics
            ):
                # applies_to entries are table-qualified in canonical
                # snapshots; bare names are accepted for hand-built ones.
                score += policy.w_semantics_token
            scores[detail.name] = score
        # Columns without details (no introspected descriptors) can still
        # match by name so legacy/hand-built snapshots remain usable.
        for name in table.columns:
            if name in scores:
                continue
            score = 0.0
            if f"{table.name}.{name}" in terms.qualified:
                score += policy.w_qualified_column
            if any(t == name for t in column_terms):
                score += policy.w_column_name_exact
            elif any(t.casefold() == name.casefold() for t in column_terms):
                score += policy.w_name_case_insensitive
            scores[name] = score
        return scores

    def _score_all_columns(
        self, snapshot: SchemaSnapshot, terms: _Terms
    ) -> dict[str, dict[str, float]]:
        return {
            table.name: self._score_columns(table, snapshot, terms)
            for table in snapshot.tables
        }

    def _select_columns(
        self,
        snapshot: SchemaSnapshot,
        column_scores: dict[str, dict[str, float]],
        selected: tuple[str, ...],
    ) -> list[ColumnRef]:
        policy = self._policy
        selected_set = set(selected)
        columns: list[ColumnRef] = []
        for table in snapshot.tables:
            if table.name not in selected_set:
                continue
            scores = column_scores.get(table.name, {})
            ordinals = {name: index for index, name in enumerate(table.columns)}
            ranked = sorted(
                (
                    name
                    for name, score in scores.items()
                    if score >= policy.min_column_score
                ),
                key=lambda name: (-scores[name], ordinals.get(name, len(ordinals)), name),
            )
            for name in ranked[: policy.column_top_k_per_table]:
                columns.append(ColumnRef(table=table.name, column=name))
        return columns

    # ------------------------------------------------------------------
    # Join paths
    # ------------------------------------------------------------------

    def _join_hints(self, snapshot: SchemaSnapshot, selected: tuple[str, ...]) -> list[JoinHint]:
        """JoinHint metadata for relationship paths between selected tables.

        Undirected BFS over snapshot relationships only; deterministic
        neighbor order (sorted); bounded depth (``max_join_path_edges``);
        no cycles (visited set); one shortest path per table pair; hints
        carry the relationship's canonical direction; no SQL is produced.
        """
        policy = self._policy
        edges: dict[str, dict[str, tuple[str, str, str, str]]] = {}
        for rel in snapshot.relationships:
            # canonical direction preserved per edge (from -> to)
            edges.setdefault(rel.from_table, {})[rel.to_table] = (
                rel.from_table, rel.from_column, rel.to_table, rel.to_column,
            )
            edges.setdefault(rel.to_table, {})[rel.from_table] = (
                rel.from_table, rel.from_column, rel.to_table, rel.to_column,
            )
        selected_set = set(selected)
        pairs = sorted((a, b) for a in selected for b in selected if a < b)
        hints: list[JoinHint] = []
        seen_edges: set[tuple[str, str, str, str]] = set()
        for source, target in pairs:
            path = self._shortest_path(edges, source, target, policy.max_join_path_edges)
            if path is None:
                continue
            for step in path:
                if step in seen_edges:
                    continue
                seen_edges.add(step)
                from_table, from_column, to_table, to_column = step
                hints.append(
                    JoinHint(
                        left=ColumnRef(table=from_table, column=from_column),
                        right=ColumnRef(table=to_table, column=to_column),
                        kind="inner",
                    )
                )
        hints.sort(key=lambda h: (h.left.table, h.left.column, h.right.table, h.right.column, h.kind))
        return hints

    @staticmethod
    def _shortest_path(
        edges: dict[str, dict[str, tuple[str, str, str, str]]],
        source: str,
        target: str,
        max_edges: int,
    ) -> list[tuple[str, str, str, str]] | None:
        """Deterministic bounded BFS; returns the edge steps or None."""
        if source == target or max_edges < 1:
            return None
        visited = {source}
        frontier: list[tuple[str, list[tuple[str, str, str, str]]]] = [(source, [])]
        for _ in range(max_edges):
            next_frontier: list[tuple[str, list[tuple[str, str, str, str]]]] = []
            for node, path in frontier:
                for neighbor in sorted(edges.get(node, ())):
                    if neighbor in visited:
                        continue
                    step = edges[node][neighbor]
                    new_path = path + [step]
                    if neighbor == target:
                        return new_path
                    visited.add(neighbor)
                    next_frontier.append((neighbor, new_path))
            frontier = next_frontier
            if not frontier:
                return None
        return None

    # ------------------------------------------------------------------
    # Ambiguity
    # ------------------------------------------------------------------

    def _ambiguity_warnings(
        self,
        table_scores: dict[str, float],
        name_signaled: set[str],
        terms: _Terms,
        selected: tuple[str, ...],
    ) -> tuple[str, ...]:
        """``SCH.AMBIGUOUS`` only for material ties under the scoring rules.

        * top score shared by ≥2 selected tables where **no tied table
          carries explicit table-name evidence** → the generic signals
          (column/label/description matches) cannot distinguish them
          (e.g. two tables owning a column of the same name);
        * ≥2 tables qualified-name-matched → conflicting strong evidence.

        A tie between tables that the question explicitly named (exact
        table-name matches) is ordinary multi-table relevance, never
        ambiguity.
        """
        if not selected:
            return ()
        strong = [t for t in selected if t in terms.qualified]
        if len(strong) >= 2:
            return (
                "SCH.AMBIGUOUS: multiple tables matched by qualified name: "
                + ", ".join(sorted(strong)),
            )
        best = max(table_scores[t] for t in selected)
        tied = sorted(
            t for t in selected if abs(table_scores[t] - best) <= _SCORE_EPSILON
        )
        if len(tied) >= 2 and not any(t in name_signaled for t in tied):
            return (
                "SCH.AMBIGUOUS: table candidates are materially tied under the "
                "scoring rules: " + ", ".join(tied),
            )
        return ()
