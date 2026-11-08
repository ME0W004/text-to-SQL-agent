# tests/test_schema/conftest.py

"""Shared fakes and fixtures for the Schema Intelligence test suite.

No test in this suite requires a live database: the metadata adapter is
exercised through an injected ``connect_fn`` and the catalog through a
fake metadata reader built from the checked-in fixture
(``fixtures/three_table_metadata.json``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.core.database.metadata import (
    DatabaseMetadata,
    DiscoveredColumn,
    DiscoveredForeignKey,
    DiscoveredPrimaryKey,
    ProbeSignature,
)
from src.core.schema.catalog import SchemaCatalog

FIXTURE_DIR = Path(__file__).parent / "fixtures"
THREE_TABLE_FIXTURE = FIXTURE_DIR / "three_table_metadata.json"
REPO_ANNOTATIONS = Path(__file__).parents[2] / "data" / "schema" / "annotations.json"


def _metadata_from_fixture(path: Path) -> DatabaseMetadata:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return DatabaseMetadata(
        columns=tuple(
            DiscoveredColumn(
                table_schema=r[0],
                table_name=r[1],
                name=r[2],
                ordinal_position=int(r[3]),
                data_type=r[4],
                nullable=r[5].upper() == "YES",
            )
            for r in raw["columns"]
        ),
        primary_keys=tuple(
            DiscoveredPrimaryKey(
                table_schema=r[0],
                table_name=r[1],
                column_name=r[2],
                key_ordinal=int(r[3]),
            )
            for r in raw.get("primary_keys", [])
        ),
        foreign_keys=tuple(
            DiscoveredForeignKey(
                constraint_name=r[0],
                from_schema=r[1],
                from_table=r[2],
                from_column=r[3],
                key_ordinal=int(r[4]),
                to_schema=r[5],
                to_table=r[6],
                to_column=r[7],
            )
            for r in raw.get("foreign_keys", [])
        ),
    )


class FakeMetadataReader:
    """Deterministic in-memory metadata reader with call counters."""

    def __init__(self, metadata: DatabaseMetadata, probe: ProbeSignature | None = None) -> None:
        self.metadata = metadata
        self.probe = probe or ProbeSignature(row_count=len(metadata.columns), checksum=1234)
        self.probe_calls = 0
        self.metadata_calls = 0
        self.probe_error: Exception | None = None
        self.metadata_error: Exception | None = None

    def read_probe(self) -> ProbeSignature:
        self.probe_calls += 1
        if self.probe_error is not None:
            raise self.probe_error
        return self.probe

    def read_metadata(self) -> DatabaseMetadata:
        self.metadata_calls += 1
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata


class FakeClock:
    """Deterministic advancing clock (one minute per call)."""

    def __init__(self) -> None:
        self._now = datetime(2026, 9, 4, 12, 0, 0)

    def __call__(self) -> datetime:
        current = self._now
        self._now = self._now + timedelta(minutes=1)
        return current


@pytest.fixture
def three_table_metadata() -> DatabaseMetadata:
    return _metadata_from_fixture(THREE_TABLE_FIXTURE)


@pytest.fixture
def fake_reader(three_table_metadata) -> FakeMetadataReader:
    return FakeMetadataReader(three_table_metadata)


@pytest.fixture
def make_catalog(tmp_path):
    """Factory building catalogs against tmp cache paths.

    Each catalog gets its own cache file unless ``cache_path`` is given
    (for tests that deliberately share a cache across instances).
    """

    counter = {"n": 0}

    def _make(
        reader,
        *,
        annotations_path: Path | str | None = None,
        cache_path: Path | str | None = None,
        history_size: int = 5,
        clock=None,
    ) -> SchemaCatalog:
        counter["n"] += 1
        if cache_path is None:
            cache_path = tmp_path / f"schema_cache_{counter['n']}.json"
        return SchemaCatalog(
            reader,
            annotations_path=str(annotations_path or REPO_ANNOTATIONS),
            cache_path=str(cache_path),
            history_size=history_size,
            clock=clock,
        )

    return _make
