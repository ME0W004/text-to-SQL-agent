# tests/test_schema/test_retrieval_evaluation.py

"""Phase 3.2 deterministic retrieval evaluation (Phases 18-19).

Runs the checked-in evaluation dataset
(``fixtures/retrieval_cases.json``) against the canonical three-table
snapshot built from the checked-in metadata fixture + repository
annotations — no live database — and computes the retrieval metrics:

* table / column precision and recall (against exact case truth),
* relationship recall,
* irrelevant-table / irrelevant-column rates,
* bounded-context compliance rate,
* full-schema leakage rate over the protected LLM-facing rendering
  path (must be exactly 0%).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from src.agents.contracts import (
    Entity,
    QuestionFilter,
    QuestionMetric,
    QuestionSpec,
    SchemaContext,
    SchemaSnapshot,
)
from src.core.schema.catalog import SchemaCatalog
from src.core.schema.renderer import SchemaContextRenderer
from src.core.schema.retriever import DEFAULT_RETRIEVAL_POLICY, SchemaRetriever, serialized_context_size

from tests.test_schema.conftest import (
    REPO_ANNOTATIONS,
    THREE_TABLE_FIXTURE,
    FakeMetadataReader,
    _metadata_from_fixture,
)

DATASET_PATH = Path(__file__).parent / "fixtures" / "retrieval_cases.json"


@dataclass
class CaseResult:
    name: str
    passed: bool
    actual_tables: tuple[str, ...] = ()
    actual_columns: tuple[tuple[str, str], ...] = ()
    expected_tables: tuple[str, ...] = ()
    expected_columns: tuple[tuple[str, str], ...] = ()
    failures: tuple[str, ...] = field(default_factory=tuple)


def _build_spec(raw: dict) -> QuestionSpec:
    return QuestionSpec(
        intent="eval",
        entities=tuple(
            Entity(
                text=e["text"],
                entity_type=e.get("entity_type", "entity"),
                qualified_name=e.get("qualified_name"),
            )
            for e in raw.get("entities", [])
        ),
        filters=tuple(
            QuestionFilter(field=f["field"], operator=f["operator"], value=f.get("value"))
            for f in raw.get("filters", [])
        ),
        metrics=tuple(
            QuestionMetric(name=m["name"], aggregation=m.get("aggregation"))
            for m in raw.get("metrics", [])
        ),
    )


@pytest.fixture(scope="module")
def real_snapshot() -> SchemaSnapshot:
    """The canonical snapshot: checked-in fixture metadata + repository
    annotations through the real SchemaCatalog (no live database)."""
    reader = FakeMetadataReader(_metadata_from_fixture(THREE_TABLE_FIXTURE))
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="retrieval-eval-"))
    catalog = SchemaCatalog(
        reader,
        annotations_path=str(REPO_ANNOTATIONS),
        cache_path=str(tmp / "cache.json"),
    )
    return catalog.get_snapshot()


def _run_case(case: dict, snapshot: SchemaSnapshot) -> tuple[CaseResult, SchemaContext]:
    spec = _build_spec(case["spec"])
    context = SchemaRetriever().retrieve(spec, snapshot)
    expected = case["expected"]
    failures: list[str] = []

    actual_tables = tuple(t.name for t in context.tables)
    expected_tables = tuple(expected["tables"])
    if sorted(actual_tables) != sorted(expected_tables):
        failures.append(f"tables {actual_tables} != {expected_tables}")

    actual_columns = tuple((c.table, c.column) for c in context.columns)
    expected_columns = tuple((c[0], c[1]) for c in expected["columns"])
    if sorted(actual_columns) != sorted(expected_columns):
        failures.append(f"columns {actual_columns} != {expected_columns}")

    actual_joins = sorted(
        (j.left.table, j.left.column, j.right.table, j.right.column) for j in context.joins
    )
    expected_joins = sorted(tuple(j) for j in expected["joins"])
    if actual_joins != expected_joins:
        failures.append(f"joins {actual_joins} != {expected_joins}")

    is_ambiguous = any(w.startswith("SCH.AMBIGUOUS") for w in context.warnings)
    if is_ambiguous != expected["ambiguous"]:
        failures.append(f"ambiguous {is_ambiguous} != {expected['ambiguous']}")

    is_empty = not actual_tables
    if is_empty != expected["empty"]:
        failures.append(f"empty {is_empty} != {expected['empty']}")

    if expected["empty"]:
        if not any(w.startswith("SCH.NO_RELEVANT_SCHEMA") for w in context.warnings):
            failures.append("empty case must carry SCH.NO_RELEVANT_SCHEMA")
        if context.confidence != 0.0:
            failures.append("empty case must carry zero confidence")

    return (
        CaseResult(
            name=case["name"],
            passed=not failures,
            actual_tables=actual_tables,
            actual_columns=actual_columns,
            expected_tables=expected_tables,
            expected_columns=expected_columns,
            failures=tuple(failures),
        ),
        context,
    )


class TestRetrievalEvaluation:
    """Per-case exactness: every dataset case must reproduce its truth."""

    @pytest.mark.parametrize(
        "case",
        json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"],
        ids=lambda c: c["name"],
    )
    def test_case_matches_expectations(self, case: dict, real_snapshot: SchemaSnapshot) -> None:
        result, _ = _run_case(case, real_snapshot)
        assert result.passed, f"{result.name}: {result.failures}"


@pytest.fixture(scope="module")
def metrics(real_snapshot: SchemaSnapshot) -> dict:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    results: list[CaseResult] = []
    contexts: dict[str, SchemaContext] = {}
    for case in dataset["cases"]:
        result, context = _run_case(case, real_snapshot)
        results.append(result)
        contexts[case["name"]] = context

    snapshot_table_count = len(real_snapshot.tables)
    table_tp = sum(
        len(set(r.actual_tables) & set(r.expected_tables)) for r in results
    )
    table_actual = sum(len(r.actual_tables) for r in results)
    table_expected = sum(len(r.expected_tables) for r in results)
    col_tp = sum(
        len(set(r.actual_columns) & set(r.expected_columns)) for r in results
    )
    col_actual = sum(len(r.actual_columns) for r in results)
    col_expected = sum(len(r.expected_columns) for r in results)

    # Bounded-context compliance: every non-empty context respects
    # the policy limits and the serialized-size budget.
    compliant = 0
    for context in contexts.values():
        if not context.tables:
            compliant += 1
            continue
        per_table: dict[str, int] = {}
        ok = len(context.tables) <= DEFAULT_RETRIEVAL_POLICY.table_top_k
        for c in context.columns:
            per_table[c.table] = per_table.get(c.table, 0) + 1
        ok = ok and all(
            n <= DEFAULT_RETRIEVAL_POLICY.column_top_k_per_table for n in per_table.values()
        )
        ok = ok and len(context.joins) <= DEFAULT_RETRIEVAL_POLICY.max_join_hints
        ok = ok and serialized_context_size(context) <= DEFAULT_RETRIEVAL_POLICY.max_context_chars
        compliant += 1 if ok else 0

    # Full-schema leakage over the protected rendering path: the
    # rendered payload may never name a snapshot table outside the
    # case's retrieved context.
    renderer = SchemaContextRenderer()
    all_snapshot_tables = {t.name for t in real_snapshot.tables}
    leaked = 0
    for context in contexts.values():
        if not context.tables:
            continue
        rendered = renderer.render(context)
        mentioned = {name for name in all_snapshot_tables if name in rendered}
        if not mentioned <= {t.name for t in context.tables}:
            leaked += 1

    return {
        "cases": len(results),
        "case_passes": sum(1 for r in results if r.passed),
        "table_precision": table_tp / table_actual if table_actual else 1.0,
        "table_recall": table_tp / table_expected if table_expected else 1.0,
        "column_precision": col_tp / col_actual if col_actual else 1.0,
        "column_recall": col_tp / col_expected if col_expected else 1.0,
        "irrelevant_table_rate": (
            (table_actual - table_tp) / table_actual if table_actual else 0.0
        ),
        "irrelevant_column_rate": (
            (col_actual - col_tp) / col_actual if col_actual else 0.0
        ),
        "bounded_context_compliance_rate": compliant / len(results),
        "full_schema_leakage_rate": leaked
        / max(1, sum(1 for c in contexts.values() if c.tables)),
        "snapshot_table_count": snapshot_table_count,
    }


class TestRetrievalMetrics:
    """Dataset-level retrieval metrics (Phase 19)."""

    def test_all_cases_pass(self, metrics: dict) -> None:
        assert metrics["case_passes"] == metrics["cases"]

    def test_precision_is_perfect_against_exact_truth(self, metrics: dict) -> None:
        # Exact-truth dataset: a production retriever must not trade
        # precision for recall — every selected table/column must be
        # justified by its case.
        assert metrics["table_precision"] == 1.0
        assert metrics["column_precision"] == 1.0
        assert metrics["irrelevant_table_rate"] == 0.0
        assert metrics["irrelevant_column_rate"] == 0.0

    def test_recall_is_perfect_against_exact_truth(self, metrics: dict) -> None:
        assert metrics["table_recall"] == 1.0
        assert metrics["column_recall"] == 1.0

    def test_relationship_recall_is_perfect(self, real_snapshot: SchemaSnapshot, metrics: dict) -> None:
        dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        required_total = 0
        required_found = 0
        for case in dataset["cases"]:
            spec = _build_spec(case["spec"])
            context = SchemaRetriever().retrieve(spec, real_snapshot)
            actual = {
                (j.left.table, j.left.column, j.right.table, j.right.column)
                for j in context.joins
            }
            for join in case["expected"]["joins"]:
                required_total += 1
                if tuple(join) in actual:
                    required_found += 1
        assert required_total > 0
        assert required_found / required_total == 1.0

    def test_bounded_context_compliance_is_total(self, metrics: dict) -> None:
        assert metrics["bounded_context_compliance_rate"] == 1.0

    def test_full_schema_leakage_rate_is_zero(self, metrics: dict) -> None:
        # Critical metric: 0% on every protected LLM-facing path.
        assert metrics["full_schema_leakage_rate"] == 0.0
