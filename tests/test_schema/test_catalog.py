# tests/test_schema/test_catalog.py

"""SchemaCatalog behavior tests (Sprint 3.1B Phase 24).

Covers: refresh flows (first/unchanged/changed/forced), probe gating,
annotation merge + drift warnings, structural precedence, stale serve,
no-cache failure, cache corruption/reuse, bounded history, version
lookup, deterministic fingerprinting/serialization, relationship and
label rules, instance isolation and thread safety. No live database.
"""

from __future__ import annotations

import json
import threading

import pytest

from src.agents.contracts import (
    BusinessSemantics,
    ColumnDescriptor,
    Relationship,
    SchemaCatalog as SchemaCatalogProtocol,
    SchemaSnapshot,
)
from src.core.database.metadata import DatabaseMetadata, DiscoveredColumn, ProbeSignature
from src.core.database.policy import DatabaseError, DatabaseErrorCode
from src.core.schema.catalog import (
    DEFAULT_ANNOTATIONS_PATH,
    SCHEMA_HISTORY_SIZE,
    SchemaCatalog,
    _fingerprint,
)

from .conftest import FakeClock, FakeMetadataReader, _metadata_from_fixture


def first_refresh(catalog):
    return catalog.get_snapshot()


class TestSuccessfulRefresh:
    def test_first_refresh_builds_snapshot_from_fixture(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        assert isinstance(snapshot, SchemaSnapshot)
        assert [t.name for t in snapshot.tables] == [
            "Sale.OrderItems", "Sale.Orders", "Sale.Shipments",
        ]
        assert len(snapshot.labels) == 48
        assert fake_reader.metadata_calls == 1

    def test_names_details_invariant_holds(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        for table in snapshot.tables:
            assert tuple(d.name for d in table.column_details) == table.columns

    def test_no_declared_constraints_means_no_pk_and_key_role_none(self, fake_reader, make_catalog):
        # The documented database declares no PK/FK constraints; the
        # snapshot must not fabricate them.
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        assert all(t.primary_key is None for t in snapshot.tables)
        assert all(d.key_role == "none" for t in snapshot.tables for d in t.column_details)

    def test_labels_are_table_qualified(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        assert all("." in key for key in snapshot.labels)
        assert snapshot.labels["Sale.Orders.Id"] == "شناسه"
        assert snapshot.labels["Sale.Shipments.Id"] == "شناسه"
        assert snapshot.labels["Sale.OrderItems.mesc"] == "کد ماده"

    def test_annotated_relationships_only(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        assert len(snapshot.relationships) == 2
        assert all(r.kind == "annotated" for r in snapshot.relationships)
        assert all(r.cardinality == "1:N" for r in snapshot.relationships)
        pairs = {(r.from_table, r.from_column, r.to_table, r.to_column) for r in snapshot.relationships}
        assert ("Sale.OrderItems", "orderId", "Sale.Orders", "Id") in pairs
        assert ("Sale.Shipments", "orderItemId", "Sale.OrderItems", "Id") in pairs

    def test_no_inferred_relationships_from_similar_names(self, fake_reader, make_catalog):
        # serialNo / Id / quantity exist in several tables; the catalog
        # must not fabricate relationships from name similarity.
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        assert len(snapshot.relationships) == 2

    def test_table_descriptions_from_annotations(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        by_name = {t.name: t for t in snapshot.tables}
        assert by_name["Sale.Orders"].description and "Order headers" in by_name["Sale.Orders"].description
        assert by_name["Sale.Shipments"].column_details[0].description  # Id annotated

    def test_business_semantics_merged(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        formulas = [s.formula for s in snapshot.business_semantics]
        assert "quantity * unitPrice" in formulas
        assert all(isinstance(s, BusinessSemantics) for s in snapshot.business_semantics)

    def test_structural_truth_wins_over_annotations(self, fake_reader, make_catalog):
        # Annotations carry no type/nullability at all; types/nullability
        # always come from discovered metadata.
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        orders = {t.name: t for t in snapshot.tables}["Sale.Orders"]
        details = {d.name: d for d in orders.column_details}
        assert details["entityDate"].data_type == "date"
        assert details["entityDate"].nullable is True


class TestRefreshSemantics:
    def test_unchanged_refresh_reuses_version_identity(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader, clock=FakeClock())
        first = first_refresh(catalog)
        second = catalog.refresh()
        assert second.version.id == first.version.id
        assert second.version == first.version  # same object content, same captured_at
        assert len(catalog.versions()) == 1
        assert fake_reader.metadata_calls == 1  # probe said unchanged → no rediscovery

    def test_probe_gate_skips_discovery(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        first_refresh(catalog)
        calls_before = fake_reader.metadata_calls
        catalog.refresh()
        assert fake_reader.metadata_calls == calls_before
        assert fake_reader.probe_calls >= 2

    def test_forced_refresh_discovers_but_reuses_identity_when_content_same(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader, clock=FakeClock())
        first = first_refresh(catalog)
        snapshot = catalog.refresh(force=True)
        assert fake_reader.metadata_calls == 2
        assert snapshot.version.id == first.version.id
        assert len(catalog.versions()) == 1

    def test_changed_metadata_creates_new_version(self, fake_reader, make_catalog, three_table_metadata):
        catalog = make_catalog(fake_reader, clock=FakeClock())
        first = first_refresh(catalog)
        # Simulate a new column discovered by the database.
        changed = DatabaseMetadata(
            columns=three_table_metadata.columns
            + (DiscoveredColumn("Sale", "Orders", "newCol", 17, "int", True),),
            primary_keys=(),
            foreign_keys=(),
        )
        fake_reader.metadata = changed
        fake_reader.probe = ProbeSignature(row_count=49, checksum=999)
        second = catalog.refresh()
        assert second.version.id != first.version.id
        assert len(catalog.versions()) == 2
        orders = {t.name: t for t in second.tables}["Sale.Orders"]
        assert orders.columns[-1] == "newCol"

    def test_annotations_change_forces_rediscovery(self, fake_reader, make_catalog, tmp_path):
        import shutil

        annotations_copy = tmp_path / "annotations.json"
        shutil.copy(DEFAULT_ANNOTATIONS_PATH, annotations_copy)
        catalog = make_catalog(fake_reader, annotations_path=annotations_copy)
        first = first_refresh(catalog)
        payload = json.loads(annotations_copy.read_text(encoding="utf-8"))
        payload["labels"]["Sale.Orders.bourseContractNo"] = "شماره قرارداد بورس (ویرایش)"
        annotations_copy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        second = catalog.refresh()
        assert second.version.id != first.version.id
        assert second.labels["Sale.Orders.bourseContractNo"].endswith("(ویرایش)")

    def test_capture_time_does_not_affect_identity(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader, clock=FakeClock())
        first = first_refresh(catalog)
        forced = catalog.refresh(force=True)  # clock advanced → different captured_at
        assert first.version.id == forced.version.id


class TestFailureSemantics:
    def test_no_cache_failure_is_fail_closed(self, tmp_path):
        reader = FakeMetadataReader(DatabaseMetadata())
        reader.probe_error = DatabaseError(DatabaseErrorCode.DATABASE_CONNECTION_ERROR, "down")
        catalog = SchemaCatalog(
            reader,
            annotations_path=str(tmp_path / "missing_annotations.json"),
            cache_path=str(tmp_path / "cache.json"),
        )
        with pytest.raises(Exception) as excinfo:
            catalog.current_version()
        assert excinfo.value.code == "SCH.UNAVAILABLE"

    def test_stale_serve_on_later_failure(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        first = first_refresh(catalog)
        fake_reader.probe_error = DatabaseError(DatabaseErrorCode.DATABASE_QUERY_ERROR, "boom")
        stale = catalog.refresh()
        assert stale.version.id == first.version.id
        assert catalog.current_version() == first.version.id

    def test_stale_recovery_on_success(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        first = first_refresh(catalog)
        fake_reader.probe_error = DatabaseError(DatabaseErrorCode.DATABASE_QUERY_ERROR, "boom")
        catalog.refresh()
        fake_reader.probe_error = None
        snapshot = catalog.refresh()
        assert snapshot.version.id == first.version.id
        assert snapshot is catalog.get_snapshot()


class TestCacheAndHistory:
    def test_second_catalog_serves_from_cache_without_database(self, fake_reader, make_catalog, tmp_path):
        shared_cache = tmp_path / "shared_cache.json"
        catalog_a = make_catalog(fake_reader, cache_path=shared_cache)
        first = first_refresh(catalog_a)
        reader_b = FakeMetadataReader(fake_reader.metadata)
        catalog_b = make_catalog(reader_b, cache_path=shared_cache)
        served = catalog_b.get_snapshot()
        assert served == first
        assert reader_b.probe_calls == 0
        assert reader_b.metadata_calls == 0

    def test_cache_corruption_is_treated_as_miss(self, fake_reader, make_catalog, tmp_path):
        cache_path = tmp_path / "corrupt.json"
        cache_path.write_text("{not json at all", encoding="utf-8")
        catalog = make_catalog(fake_reader, cache_path=cache_path)
        snapshot = first_refresh(catalog)
        assert len(snapshot.tables) == 3
        # Cache was rebuilt.
        rebuilt = json.loads(cache_path.read_text(encoding="utf-8"))
        assert rebuilt["current"] == snapshot.version.id

    def test_history_is_bounded_and_prunes_oldest(self, fake_reader, make_catalog, three_table_metadata):
        catalog = make_catalog(fake_reader, history_size=3, clock=FakeClock())
        ids = []
        first = first_refresh(catalog)
        ids.append(first.version.id)
        for i, count in enumerate((49, 50, 51, 52)):
            changed = DatabaseMetadata(
                columns=three_table_metadata.columns
                + (DiscoveredColumn("Sale", "Orders", f"col{i}", 17, "int", True),),
                primary_keys=(),
                foreign_keys=(),
            )
            fake_reader.metadata = changed
            fake_reader.probe = ProbeSignature(row_count=count, checksum=i)
            ids.append(catalog.refresh(force=True).version.id)
        assert len(catalog.versions()) == 3
        assert catalog.current_version() == ids[-1]
        # Oldest two pruned.
        from src.core.schema.catalog import SchemaError
        with pytest.raises(Exception) as excinfo:
            catalog.get_snapshot(ids[0])
        assert excinfo.value.code == "SCH.VERSION_NOT_FOUND"
        assert catalog.get_snapshot(ids[-2]).version.id == ids[-2]

    def test_version_not_found_for_unknown(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        first_refresh(catalog)
        from src.core.schema.catalog import SchemaError
        with pytest.raises(Exception) as excinfo:
            catalog.get_snapshot("deadbeef")
        assert excinfo.value.code == "SCH.VERSION_NOT_FOUND"

    def test_default_history_constant(self):
        assert SCHEMA_HISTORY_SIZE >= 1


class TestDeterminism:
    def test_fingerprint_independent_of_metadata_row_order(self, make_catalog, three_table_metadata, tmp_path):
        metadata_b = DatabaseMetadata(
            columns=tuple(reversed(three_table_metadata.columns)),
            primary_keys=three_table_metadata.primary_keys,
            foreign_keys=three_table_metadata.foreign_keys,
        )
        cat_a = make_catalog(FakeMetadataReader(three_table_metadata), cache_path=tmp_path / "a.json")
        cat_b = make_catalog(FakeMetadataReader(metadata_b), cache_path=tmp_path / "b.json")
        assert cat_a.get_snapshot().version.id == cat_b.get_snapshot().version.id

    def test_fingerprint_changes_on_annotation_content_change(self, fake_reader, make_catalog, tmp_path):
        import shutil

        annotations_copy = tmp_path / "annotations.json"
        shutil.copy(DEFAULT_ANNOTATIONS_PATH, annotations_copy)
        catalog = make_catalog(fake_reader, annotations_path=annotations_copy)
        first = first_refresh(catalog)
        payload = json.loads(annotations_copy.read_text(encoding="utf-8"))
        payload["tables"]["Sale.Orders"]["description"] = "Edited description"
        annotations_copy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        second = catalog.refresh(force=True)
        assert second.version.id != first.version.id

    def test_fingerprint_excludes_version_block(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        snapshot = first_refresh(catalog)
        content = catalog._contents[snapshot.version.id]
        direct = _fingerprint(content)
        assert direct == snapshot.version.id

    def test_serialization_round_trip_through_cache(self, fake_reader, make_catalog, tmp_path):
        shared = tmp_path / "rt.json"
        catalog_a = make_catalog(fake_reader, cache_path=shared)
        first = first_refresh(catalog_a)
        catalog_b = make_catalog(FakeMetadataReader(fake_reader.metadata), cache_path=shared)
        served = catalog_b.get_snapshot()
        assert served == first
        assert served.version.id == first.version.id


class TestAnnotationDrift:
    def test_unknown_table_annotation_is_dropped_and_warned(self, fake_reader, make_catalog, tmp_path):
        annotations = tmp_path / "annotations.json"
        annotations.write_text(json.dumps({
            "tables": {"Sale.Ghost": {"description": "does not exist", "columns": {}}},
            "relationships": [],
            "business_semantics": [],
            "labels": {},
        }, ensure_ascii=False), encoding="utf-8")
        catalog = make_catalog(fake_reader, annotations_path=annotations)
        snapshot = first_refresh(catalog)
        assert "Sale.Ghost" not in {t.name for t in snapshot.tables}
        assert any("unknown table Sale.Ghost" in w for w in catalog.last_merge_warnings)

    def test_unknown_column_annotation_is_dropped_and_warned(self, fake_reader, make_catalog, tmp_path):
        annotations = tmp_path / "annotations.json"
        annotations.write_text(json.dumps({
            "tables": {"Sale.Orders": {"description": None, "columns": {"ghostCol": {"description": "x"}}}},
            "relationships": [],
            "business_semantics": [],
            "labels": {},
        }, ensure_ascii=False), encoding="utf-8")
        catalog = make_catalog(fake_reader, annotations_path=annotations)
        snapshot = first_refresh(catalog)
        orders = {t.name: t for t in snapshot.tables}["Sale.Orders"]
        assert "ghostCol" not in orders.columns
        assert any("unknown column Sale.Orders.ghostCol" in w for w in catalog.last_merge_warnings)

    def test_unknown_relationship_annotation_is_dropped_and_warned(self, fake_reader, make_catalog, tmp_path):
        # Documented relationships are kept; a fabricated one referencing
        # a nonexistent table is dropped with a warning.
        annotations = tmp_path / "annotations.json"
        annotations.write_text(json.dumps({
            "tables": {},
            "relationships": [
                {"from_table": "Sale.OrderItems", "from_column": "orderId",
                 "to_table": "Sale.Orders", "to_column": "Id", "cardinality": "1:N"},
                {"from_table": "Sale.Orders", "from_column": "buyerId",
                 "to_table": "Sale.Buyers", "to_column": "Id", "cardinality": "1:N"},
            ],
            "business_semantics": [],
            "labels": {},
        }, ensure_ascii=False), encoding="utf-8")
        catalog = make_catalog(fake_reader, annotations_path=annotations)
        snapshot = first_refresh(catalog)
        assert len(snapshot.relationships) == 1
        assert snapshot.relationships[0].kind == "annotated"
        assert any("unknown table" in w for w in catalog.last_merge_warnings)

    def test_missing_annotations_file_yields_structural_only_snapshot(self, fake_reader, make_catalog, tmp_path):
        catalog = make_catalog(fake_reader, annotations_path=tmp_path / "missing.json")
        snapshot = first_refresh(catalog)
        assert len(snapshot.tables) == 3
        assert snapshot.labels == {}
        assert snapshot.business_semantics == ()
        assert len(snapshot.relationships) == 0

    def test_fk_discovered_from_database_wins_over_annotation(self, fake_reader, make_catalog, tmp_path):
        # If the DB ever declares a constraint for a documented logical
        # relationship, the constraint version must be canonical and the
        # annotation must not duplicate it.
        annotations = tmp_path / "annotations.json"
        annotations.write_text(json.dumps({
            "tables": {},
            "relationships": [
                {"from_table": "Sale.OrderItems", "from_column": "orderId",
                 "to_table": "Sale.Orders", "to_column": "Id", "cardinality": "1:N"},
            ],
            "business_semantics": [],
            "labels": {},
        }, ensure_ascii=False), encoding="utf-8")
        constrained = DatabaseMetadata(
            columns=fake_reader.metadata.columns,
            primary_keys=(),
            foreign_keys=(),  # start with none
        )
        from src.core.database.metadata import DiscoveredForeignKey
        constrained = DatabaseMetadata(
            columns=constrained.columns,
            primary_keys=(),
            foreign_keys=(
                DiscoveredForeignKey(
                    constraint_name="FK_OrderItems_Orders",
                    from_schema="Sale", from_table="OrderItems", from_column="orderId",
                    key_ordinal=1,
                    to_schema="Sale", to_table="Orders", to_column="Id",
                ),
            ),
        )
        reader = FakeMetadataReader(constrained)
        catalog = make_catalog(reader, annotations_path=annotations)
        snapshot = first_refresh(catalog)
        matching = [r for r in snapshot.relationships
                    if (r.from_table, r.from_column, r.to_table, r.to_column)
                    == ("Sale.OrderItems", "orderId", "Sale.Orders", "Id")]
        assert len(matching) == 1
        assert matching[0].kind == "database_constraint"
        assert matching[0].cardinality is None


class TestIsolationAndConcurrency:
    def test_instances_do_not_share_mutable_state(self, fake_reader, make_catalog, three_table_metadata):
        cat_a = make_catalog(fake_reader, clock=FakeClock())
        cat_b = make_catalog(FakeMetadataReader(three_table_metadata), clock=FakeClock())
        first_refresh(cat_a)
        first_refresh(cat_b)
        assert cat_a.get_snapshot() == cat_b.get_snapshot()
        assert cat_a is not cat_b
        assert cat_a._snapshots is not cat_b._snapshots

    def test_refresh_state_isolated_per_instance(self, fake_reader, make_catalog, three_table_metadata):
        cat_a = make_catalog(fake_reader)
        cat_b = make_catalog(FakeMetadataReader(three_table_metadata))
        first_refresh(cat_a)
        first_refresh(cat_b)
        cat_a.refresh(force=True)
        assert len(cat_b.versions()) == 1

    def test_concurrent_refresh_is_safe(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        results = []
        errors = []

        def worker():
            try:
                results.append(catalog.refresh(force=True).version.id)
            except Exception as exc:  # pragma: no cover - failure captured
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(set(results)) == 1  # same content → same identity
        assert len(catalog.versions()) == 1


class TestProtocolConformance:
    def test_catalog_satisfies_canonical_protocol(self, fake_reader, make_catalog):
        catalog = make_catalog(fake_reader)
        assert isinstance(catalog, SchemaCatalogProtocol)

    def test_additive_contract_compatibility(self):
        # Pre-3.1B construction styles remain valid.
        from src.agents.contracts import TableDescriptor
        old_table = TableDescriptor(name="Sale.Orders", columns=("Id",), primary_key="Id")
        assert old_table.column_details == ()
        assert old_table.description is None
        old_rel = Relationship(
            from_table="A", from_column="x", to_table="B", to_column="y"
        )
        assert old_rel.kind == "annotated"
        assert old_rel.cardinality is None

    def test_catalog_methods_match_protocol_signatures(self):
        # Signature parity (not merely isinstance): every Protocol method's
        # positional parameter sequence must be a prefix of the
        # implementation's. The implementation may add trailing
        # keyword-only parameters with defaults (e.g. ``refresh(force)``).
        import inspect

        for method_name in ("current_version", "versions", "get_snapshot", "refresh"):
            proto_sig = inspect.signature(getattr(SchemaCatalogProtocol, method_name))
            impl_sig = inspect.signature(getattr(SchemaCatalog, method_name))
            proto_params = [
                p for p in proto_sig.parameters.values() if p.name != "self"
            ]
            impl_params = [
                p for p in impl_sig.parameters.values() if p.name != "self"
            ]
            impl_positional = [p for p in impl_params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            assert [p.name for p in proto_params] == [p.name for p in impl_positional[: len(proto_params)]]

    def test_column_descriptor_value_object_properties(self):
        a = ColumnDescriptor(name="Id", data_type="int", nullable=False)
        b = ColumnDescriptor(name="Id", data_type="int", nullable=False)
        assert a == b
        with pytest.raises(Exception):
            a.name = "changed"  # frozen
