# tests/test_schema/test_retrieval_determinism.py

"""Phase 3.2 determinism, concurrency and performance tests.

Pins the invariant:

    same QuestionSpec + same SchemaSnapshot + same RetrievalPolicy
        = same SchemaContext

across process variation (PYTHONHASHSEED), label-dictionary ordering,
repeated runs and concurrent execution — and verifies that retrieval
cost stays bounded on oversized synthetic snapshots while the context
stays bounded by policy.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from src.agents.contracts import (
    BusinessSemantics,
    ColumnDescriptor,
    ColumnRef,
    Entity,
    QuestionSpec,
    Relationship,
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def make_snapshot(labels: dict[str, str] | None = None) -> SchemaSnapshot:
    return SchemaSnapshot(
        version=SchemaVersion(id="det", captured_at=datetime(2026, 9, 4), source="fixture"),
        tables=(
            TableDescriptor(
                name="Sale.Orders",
                description="Order headers: one row per sales order.",
                columns=("Id", "buyer", "status"),
                primary_key="Id",
                column_details=(
                    ColumnDescriptor("Id", "int", False, key_role="pk"),
                    ColumnDescriptor("buyer", "nvarchar", False, description="Customer name"),
                    ColumnDescriptor("status", "nvarchar", False, description="Lifecycle status"),
                ),
            ),
            TableDescriptor(
                name="Sale.OrderItems",
                description="Order line items: quantity, price.",
                columns=("Id", "orderId", "quantity", "unitPrice"),
                primary_key="Id",
                column_details=(
                    ColumnDescriptor("Id", "int", False, key_role="pk"),
                    ColumnDescriptor("orderId", "int", False, key_role="fk"),
                    ColumnDescriptor("quantity", "float", True, description="Ordered quantity"),
                    ColumnDescriptor("unitPrice", "decimal", True, description="Price per unit"),
                ),
            ),
        ),
        relationships=(
            Relationship("Sale.OrderItems", "orderId", "Sale.Orders", "Id", kind="annotated"),
        ),
        business_semantics=(
            BusinessSemantics(
                description="Revenue is quantity times unitPrice",
                formula="quantity * unitPrice",
                applies_to=("Sale.OrderItems.quantity", "Sale.OrderItems.unitPrice"),
            ),
        ),
        labels=labels
        or {
            "Sale.Orders.buyer": "خریدار",
            "Sale.OrderItems.quantity": "تعداد",
        },
    )


SPECS = {
    "named_table": QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)),
    "persian_label": QuestionSpec(intent="a", entities=(Entity("خریدار", entity_type="entity"),)),
    "semantics_metric": QuestionSpec(
        intent="a", entities=(Entity("revenue", entity_type="metric"),)
    ),
    "qualified": QuestionSpec(
        intent="a",
        entities=(Entity("OrderItems", entity_type="table", qualified_name="Sale.OrderItems"),),
    ),
}


def _serialize(context) -> str:
    return json.dumps(
        {
            "tables": [t.name for t in context.tables],
            "columns": [[c.table, c.column] for c in context.columns],
            "joins": [[j.left.table, j.left.column, j.right.table, j.right.column] for j in context.joins],
            "semantics": [s.description for s in context.relevant_business_semantics],
            "confidence": context.confidence,
            "warnings": list(context.warnings),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Determinism (Phase 17)
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize("spec_name", sorted(SPECS))
    def test_repeated_runs_identical(self, spec_name):
        retriever = SchemaRetriever()
        snapshot = make_snapshot()
        first = retriever.retrieve(SPECS[spec_name], snapshot)
        for _ in range(9):
            assert retriever.retrieve(SPECS[spec_name], snapshot) == first

    def test_process_variation_hash_seed(self):
        """Retrieval output is identical under different PYTHONHASHSEED
        values (frozenset/dict iteration order must never leak)."""
        script = (
            "import json\n"
            "from datetime import datetime\n"
            "from src.agents.contracts import (\n"
            "    BusinessSemantics, ColumnDescriptor, Entity, QuestionSpec,\n"
            "    Relationship, SchemaSnapshot, SchemaVersion, TableDescriptor,\n"
            ")\n"
            "from src.core.schema.retriever import SchemaRetriever\n"
            f"sys_path = {str(PROJECT_ROOT)!r}\n"
            "import sys\n"
            "sys.path.insert(0, sys_path)\n"
            "snapshot = SchemaSnapshot(\n"
            "    version=SchemaVersion(id='d', captured_at=datetime(2026, 9, 4), source='f'),\n"
            "    tables=(\n"
            "        TableDescriptor(name='Sale.Orders', description='Order headers.',\n"
            "                        columns=('Id', 'buyer', 'status'), primary_key='Id',\n"
            "                        column_details=(\n"
            "                            ColumnDescriptor('Id', 'int', False, key_role='pk'),\n"
            "                            ColumnDescriptor('buyer', 'nvarchar', False, description='Customer name'),\n"
            "                            ColumnDescriptor('status', 'nvarchar', False, description='Lifecycle status'),)),\n"
            "        TableDescriptor(name='Sale.OrderItems', description='Line items: quantity, price.',\n"
            "                        columns=('Id', 'orderId', 'quantity', 'unitPrice'), primary_key='Id',\n"
            "                        column_details=(\n"
            "                            ColumnDescriptor('Id', 'int', False, key_role='pk'),\n"
            "                            ColumnDescriptor('orderId', 'int', False, key_role='fk'),\n"
            "                            ColumnDescriptor('quantity', 'float', True, description='Ordered quantity'),\n"
            "                            ColumnDescriptor('unitPrice', 'decimal', True, description='Price per unit'),)),\n"
            "    ),\n"
            "    relationships=(Relationship('Sale.OrderItems', 'orderId', 'Sale.Orders', 'Id', kind='annotated'),),\n"
            "    business_semantics=(BusinessSemantics(\n"
            "        description='Revenue is quantity times unitPrice', formula='quantity * unitPrice',\n"
            "        applies_to=('Sale.OrderItems.quantity', 'Sale.OrderItems.unitPrice')),),\n"
            "    labels={'Sale.Orders.buyer': 'خریدار', 'Sale.OrderItems.quantity': 'تعداد'},\n"
            ")\n"
            "specs = [\n"
            "    QuestionSpec(intent='a', entities=(Entity('Orders', entity_type='table'),)),\n"
            "    QuestionSpec(intent='a', entities=(Entity('خریدار', entity_type='entity'),)),\n"
            "    QuestionSpec(intent='a', entities=(Entity('revenue', entity_type='metric'),)),\n"
            "    QuestionSpec(intent='a', filters=[], metrics=[]),\n"
            "]\n"
            "out = []\n"
            "for spec in specs:\n"
            "    ctx = SchemaRetriever().retrieve(spec, snapshot)\n"
            "    out.append([t.name for t in ctx.tables])\n"
            "    out.append([c.column for c in ctx.columns])\n"
            "    out.append([s.description for s in ctx.relevant_business_semantics])\n"
            "print(json.dumps(out, sort_keys=True, ensure_ascii=False))\n"
        )
        outputs = []
        for seed in ("0", "1", "42", "12345"):
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                cwd=PROJECT_ROOT,
                env={"PYTHONHASHSEED": seed, "PATH": "", "SYSTEMROOT": "C:\\Windows"},
                timeout=120,
            )
            assert result.returncode == 0, result.stderr
            outputs.append(result.stdout.strip().splitlines()[-1])
        assert len(set(outputs)) == 1, f"hash-seed variation changed retrieval: {outputs}"

    def test_label_dict_insertion_order_irrelevant(self):
        spec = QuestionSpec(intent="a", entities=(Entity("خریدار", entity_type="entity"),))
        labels_a = {
            "Sale.Orders.buyer": "خریدار",
            "Sale.OrderItems.quantity": "تعداد",
        }
        labels_b = {
            "Sale.OrderItems.quantity": "تعداد",
            "Sale.Orders.buyer": "خریدار",
        }
        ctx_a = SchemaRetriever().retrieve(spec, make_snapshot(labels_a))
        ctx_b = SchemaRetriever().retrieve(spec, make_snapshot(labels_b))
        assert ctx_a == ctx_b

    def test_equal_score_tie_order_deterministic(self):
        # Two structurally identical tables: the lexicographically-first
        # qualified name must always come first.
        snapshot = SchemaSnapshot(
            version=SchemaVersion(id="tie", captured_at=datetime(2026, 9, 4), source="f"),
            tables=(
                TableDescriptor(name="Sale.ZTable", description="Records.", columns=("Id", "flag")),
                TableDescriptor(name="Sale.ATable", description="Records.", columns=("Id", "flag")),
            ),
        )
        spec = QuestionSpec(intent="a", entities=(Entity("flag", entity_type="column"),))
        ctx = SchemaRetriever().retrieve(spec, snapshot)
        assert [t.name for t in ctx.tables] == ["Sale.ATable", "Sale.ZTable"]
        again = SchemaRetriever().retrieve(spec, snapshot)
        assert again == ctx

    def test_duplicate_relationship_rows_do_not_change_output(self):
        base = make_snapshot()
        duplicated = SchemaSnapshot(
            version=base.version,
            tables=base.tables,
            relationships=base.relationships + (base.relationships[0], base.relationships[0]),
            business_semantics=base.business_semantics,
            labels=base.labels,
        )
        spec = SPECS["named_table"]
        assert SchemaRetriever().retrieve(spec, duplicated) == SchemaRetriever().retrieve(spec, base)


# ---------------------------------------------------------------------------
# Concurrency and isolation (Phase 22)
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_parallel_retrievals_are_isolated_and_identical(self):
        retriever = SchemaRetriever()
        snapshot = make_snapshot()
        serial = {name: retriever.retrieve(spec, snapshot) for name, spec in SPECS.items()}

        results: dict[str, list] = {name: [] for name in SPECS}
        lock = threading.Lock()

        def worker(spec_name: str) -> None:
            for _ in range(20):
                ctx = retriever.retrieve(SPECS[spec_name], snapshot)
                with lock:
                    results[spec_name].append(ctx)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for name in SPECS:
                for _ in range(4):
                    futures.append(pool.submit(worker, name))
            for future in futures:
                future.result()

        for name, contexts in results.items():
            assert len(contexts) == 80
            assert all(ctx == serial[name] for ctx in contexts), (
                f"concurrent retrieval for {name} diverged from the serial result"
            )

    def test_policy_is_immutable_and_unaffected(self):
        policy = RetrievalPolicy()
        retriever = SchemaRetriever(policy)
        snapshot = make_snapshot()
        retriever.retrieve(SPECS["named_table"], snapshot)
        with pytest.raises(Exception):
            policy.table_top_k = 99  # type: ignore[misc]
        assert DEFAULT_RETRIEVAL_POLICY.table_top_k == 3
        # The default policy object is never mutated by retrieval.
        assert retriever._policy is policy
        assert policy == RetrievalPolicy()

    def test_contexts_do_not_share_state_across_requests(self):
        retriever = SchemaRetriever()
        snapshot = make_snapshot()
        ctx_a = retriever.retrieve(SPECS["named_table"], snapshot)
        ctx_b = retriever.retrieve(SPECS["persian_label"], snapshot)
        assert ctx_a is not ctx_b
        assert ctx_a.tables != ctx_b.tables or ctx_a.columns != ctx_b.columns
        # Frozen value objects cannot be mutated across requests.
        with pytest.raises(Exception):
            ctx_a.tables[0].name = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Performance and bounded scaling (Phase 28)
# ---------------------------------------------------------------------------


def _big_snapshot(tables: int, columns_per_table: int) -> SchemaSnapshot:
    descriptors = []
    for i in range(tables):
        name = f"Sale.T{i:04d}"
        cols = tuple(f"c{j:03d}" for j in range(columns_per_table))
        descriptors.append(
            TableDescriptor(
                name=name,
                description=f"Synthetic table number {i} with filler words for tokens.",
                columns=cols,
                primary_key="c000",
                column_details=tuple(
                    ColumnDescriptor(c, "int", False, description=f"Column {j} of table {i}")
                    for j, c in enumerate(cols)
                ),
            )
        )
    relationships = tuple(
        Relationship(f"Sale.T{i:04d}", "c000", f"Sale.T{i + 1:04d}", "c000")
        for i in range(tables - 1)
    )
    return SchemaSnapshot(
        version=SchemaVersion(id="big", captured_at=datetime(2026, 9, 4), source="synthetic"),
        tables=tuple(descriptors),
        relationships=relationships,
    )


class TestPerformance:
    def test_retrieval_cost_bounded_on_large_snapshots(self):
        # 200 tables x 50 columns (10k columns): the retrieval wall time
        # stays within a generous bound and the CONTEXT stays bounded by
        # the policy regardless of catalog size.
        import time

        snapshot = _big_snapshot(tables=200, columns_per_table=50)
        spec = QuestionSpec(
            intent="a",
            entities=(Entity("T0100", entity_type="table"), Entity("c007", entity_type="column")),
        )
        started = time.perf_counter()
        ctx = SchemaRetriever().retrieve(spec, snapshot)
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0, f"retrieval on a 200x50 snapshot took {elapsed:.3f}s"
        assert len(ctx.tables) <= DEFAULT_RETRIEVAL_POLICY.table_top_k
        per_table: dict[str, int] = {}
        for c in ctx.columns:
            per_table[c.table] = per_table.get(c.table, 0) + 1
        assert all(n <= DEFAULT_RETRIEVAL_POLICY.column_top_k_per_table for n in per_table.values())
        assert serialized_context_size(ctx) <= DEFAULT_RETRIEVAL_POLICY.max_context_chars

    def test_serialization_latency_bounded(self):
        import time

        ctx = SchemaRetriever().retrieve(
            QuestionSpec(intent="a", entities=(Entity("Orders", entity_type="table"),)),
            make_snapshot(),
        )
        started = time.perf_counter()
        for _ in range(1000):
            serialized_context_size(ctx)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"1000 serializations took {elapsed:.3f}s"

    def test_context_size_constant_as_catalog_grows(self):
        # LLM payload size scales with the bounded context, not the
        # total schema size.
        spec = QuestionSpec(
            intent="a",
            entities=(Entity("T0010", entity_type="table"), Entity("c005", entity_type="column")),
        )
        small = SchemaRetriever().retrieve(spec, _big_snapshot(50, 20))
        large = SchemaRetriever().retrieve(spec, _big_snapshot(200, 50))
        assert serialized_context_size(large) <= serialized_context_size(small) * 1.5
