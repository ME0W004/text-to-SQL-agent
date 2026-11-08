# src/core/schema/catalog.py

"""Canonical SchemaCatalog implementation (ADR-017, Sprint 3.1B).

Single runtime owner of the versioned schema snapshot. The catalog:

* loads user-authored semantic annotations (``data/schema/annotations.json``);
* serves the cached snapshot without touching the database when possible;
* refreshes through the injected metadata reader — probe first, full
  discovery only when the probe (or forced refresh) says content changed;
* merges structural truth (database metadata — always authoritative for
  existence/types/nullability/keys) with annotation truth (descriptions,
  labels, semantics, documented logical relationships), recording every
  annotation that references unknown schema objects as a structured
  warning instead of silently discarding or trusting it;
* versions snapshots by SHA-256 content fingerprint over a canonical
  serialization (``captured_at`` and the version block itself excluded);
* keeps a bounded on-disk, content-addressed snapshot history in
  ``data/schema/schema_cache.json`` (the legacy ``.schema_cache.json``
  of the column-map pipeline is untouched).

Boundaries (enforced by ``tests/test_architecture/test_schema_boundary.py``):
no ``pymssql`` import, no ``src.core.llm`` import, no ``src.agents.state``
import, no import-time I/O, no LLM anywhere.

Failure semantics (Phase 16 of the 3.1B task):

* refresh failure **with** a cached snapshot → serve it stale, emit
  ``schema_refresh_failed`` with ``stale_served=true``;
* refresh failure **without** any snapshot → typed ``SchemaError`` with
  code ``SCH.UNAVAILABLE`` (fail-closed; no empty schema is fabricated);
* unknown requested version → ``SCH.VERSION_NOT_FOUND``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, Mapping, Optional, Protocol

from src.agents.contracts import (
    BusinessSemantics,
    ColumnDescriptor,
    Relationship,
    SchemaError,
    SchemaSnapshot,
    SchemaVersion,
    TableDescriptor,
)
from src.utils.logger import log_event

__all__ = ["SchemaCatalog", "SCHEMA_HISTORY_SIZE", "DEFAULT_ANNOTATIONS_PATH", "DEFAULT_CACHE_PATH"]


# Centralized retention constant (Phase 15): newest N snapshots retained.
SCHEMA_HISTORY_SIZE = 5

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DEFAULT_ANNOTATIONS_PATH = os.path.join(_REPO_ROOT, "data", "schema", "annotations.json")
DEFAULT_CACHE_PATH = os.path.join(_REPO_ROOT, "data", "schema", "schema_cache.json")

_CACHE_VERSION = 1


class MetadataReader(Protocol):
    """Structural surface the catalog needs from the metadata adapter.

    Satisfied by ``src.core.database.metadata.DatabaseMetadataReader``
    (structurally — no import in either direction) and by test fakes.
    """

    def read_probe(self) -> Any: ...

    def read_metadata(self) -> Any: ...


@dataclass(frozen=True)
class _CacheEntry:
    """One content-addressed snapshot in the cache/history."""

    version: SchemaVersion
    content: dict


class SchemaCatalog:
    """Owns refresh, annotations, merge, fingerprinting, cache, history.

    Construction performs local-file reads only (annotations + cache) —
    no database connection, no network, no LLM. The first access
    (``current_version`` / ``versions`` / ``get_snapshot``) triggers the
    startup refresh flow from the approved design: cache hit → serve;
    cache miss → probe + full discovery. Nothing accesses the catalog in
    the live request pipeline in Sprint 3.1B (composition is additive).
    """

    def __init__(
        self,
        metadata_reader: MetadataReader,
        *,
        annotations_path: str = DEFAULT_ANNOTATIONS_PATH,
        cache_path: str = DEFAULT_CACHE_PATH,
        history_size: int = SCHEMA_HISTORY_SIZE,
        clock: Optional[Any] = None,
    ) -> None:
        self._reader = metadata_reader
        self._annotations_path = annotations_path
        self._cache_path = cache_path
        self._history_size = max(1, int(history_size))
        self._clock = clock or datetime.utcnow
        self._lock = threading.RLock()
        self._annotations: dict = {}
        self._annotations_hash: Optional[str] = None
        self._snapshots: dict[str, SchemaSnapshot] = {}
        self._contents: dict[str, dict] = {}
        self._order: list[str] = []  # oldest → newest
        self._current_id: Optional[str] = None
        self._probe: Optional[dict] = None
        self._stale = False
        self.last_merge_warnings: tuple[str, ...] = ()
        self._load_annotations()
        self._load_cache()

    # ------------------------------------------------------------------
    # Read surface (canonical SchemaCatalog Protocol)
    # ------------------------------------------------------------------

    def current_version(self) -> str:
        with self._lock:
            self._ensure_ready()
            assert self._current_id is not None  # _ensure_ready guarantees
            return self._current_id

    def versions(self) -> tuple[SchemaVersion, ...]:
        with self._lock:
            self._ensure_ready()
            return tuple(self._snapshots[v].version for v in self._order)

    def get_snapshot(self, version: Optional[str] = None) -> SchemaSnapshot:
        with self._lock:
            self._ensure_ready()
            key = version if version is not None else self._current_id
            snapshot = self._snapshots.get(key) if key is not None else None
            if snapshot is None:
                raise SchemaError(
                    f"Schema version not found: {version!r}.",
                    code="SCH.VERSION_NOT_FOUND",
                )
            return snapshot

    def refresh(self, force: bool = False) -> SchemaSnapshot:
        """Refresh the catalog.

        Probe-gated unless ``force=True``: when the stored probe
        signature (database columns aggregate + annotations file hash)
        matches the live probe, full discovery is skipped and the current
        snapshot is reused. Unchanged content always reuses the existing
        version identity; only content changes create new versions.
        """
        with self._lock:
            return self._refresh(force=force)

    # ------------------------------------------------------------------
    # Refresh internals
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        if self._current_id is not None:
            return
        # Startup flow: cache already loaded at construction; if it held
        # a current snapshot we are ready. Otherwise full discovery (a
        # probe comparison is meaningless without a baseline).
        self._refresh(force=True)

    def _refresh(self, *, force: bool) -> SchemaSnapshot:
        started = perf_counter()
        log_event("schema_refresh_started", component="schema")
        try:
            self._reload_annotations_if_changed()
            probe = self._reader.read_probe()
            probe_signature = {
                "row_count": int(probe.row_count),
                "checksum": None if probe.checksum is None else int(probe.checksum),
                "annotations_hash": self._annotations_hash,
            }
            reusable = (
                not force
                and self._probe is not None
                and probe_signature == self._probe
                and self._current_id is not None
            )
            if reusable:
                snapshot = self._snapshots[self._current_id]
                reason = "probe_unchanged"
                self._stale = False
            else:
                metadata = self._reader.read_metadata()
                content, warnings = self._build_content(metadata)
                fingerprint = _fingerprint(content)
                existing = self._snapshots.get(fingerprint)
                if existing is not None and fingerprint == self._current_id:
                    snapshot = existing  # unchanged content → same identity
                    reason = "unchanged"
                else:
                    version = SchemaVersion(
                        id=fingerprint,
                        captured_at=self._clock(),
                        source="introspection+annotations",
                    )
                    snapshot = _snapshot_from_content(version, content)
                    self._register(snapshot, content)
                    reason = "changed"
                self._probe = probe_signature
                self._stale = False
                self.last_merge_warnings = tuple(warnings)
                self._save_cache()
            self._emit_completed(reason, snapshot, started)
            return snapshot
        except Exception as exc:
            return self._handle_refresh_failure(exc, started)

    def _handle_refresh_failure(self, exc: Exception, started: float) -> SchemaSnapshot:
        duration_ms = round((perf_counter() - started) * 1000, 3)
        if self._current_id is not None:
            self._stale = True
            log_event(
                "schema_refresh_failed",
                level=logging.WARNING,
                component="schema",
                error_type=type(exc).__name__,
                stale_served=True,
                version=self._current_id,
                duration_ms=duration_ms,
            )
            return self._snapshots[self._current_id]
        log_event(
            "schema_refresh_failed",
            level=logging.ERROR,
            component="schema",
            error_type=type(exc).__name__,
            stale_served=False,
            duration_ms=duration_ms,
        )
        raise SchemaError(
            "Schema information is not available.",
            code="SCH.UNAVAILABLE",
            cause=exc,
        ) from exc

    def _emit_completed(self, reason: str, snapshot: SchemaSnapshot, started: float) -> None:
        log_event(
            "schema_refresh_completed",
            component="schema",
            reason=reason,
            version=snapshot.version.id,
            table_count=len(snapshot.tables),
            relationship_count=len(snapshot.relationships),
            stale=self._stale,
            duration_ms=round((perf_counter() - started) * 1000, 3),
        )

    # ------------------------------------------------------------------
    # Merge: structural truth (metadata) + annotation truth
    # ------------------------------------------------------------------

    def _build_content(self, metadata: Any) -> tuple[dict, tuple[str, ...]]:
        """Build the canonical content dict (fingerprint input) from
        discovered metadata plus annotations. Returns (content, warnings).
        """
        warnings: list[str] = []
        ann = self._annotations or {}
        ann_tables: dict = ann.get("tables", {}) or {}

        # --- structural grouping -------------------------------------
        columns_by_table: dict[tuple[str, str], list] = {}
        for col in metadata.columns:
            columns_by_table.setdefault((col.table_schema, col.table_name), []).append(col)
        pk_by_table: dict[tuple[str, str], list] = {}
        for pk in metadata.primary_keys:
            pk_by_table.setdefault((pk.table_schema, pk.table_name), []).append(pk)
        for key in pk_by_table:
            pk_by_table[key].sort(key=lambda p: (p.key_ordinal, p.column_name))
        fk_columns_by_table: dict[tuple[str, str], list] = {}
        for fk in metadata.foreign_keys:
            fk_columns_by_table.setdefault((fk.from_schema, fk.from_table), []).append(fk)

        # --- tables ----------------------------------------------------
        tables_content: list[dict] = []
        column_names_by_table: dict[str, set[str]] = {}
        for schema, table in sorted(columns_by_table):
            qualified = f"{schema}.{table}"
            cols = sorted(columns_by_table[(schema, table)], key=lambda c: (c.ordinal_position, c.name))
            column_names_by_table[qualified] = {c.name for c in cols}
            pk_names = {p.column_name for p in pk_by_table.get((schema, table), ())}
            fk_columns = {f.from_column for f in fk_columns_by_table.get((schema, table), ())}
            ann_table = ann_tables.get(qualified, {})
            ann_columns = ann_table.get("columns", {}) or {}
            for unknown in sorted(set(ann_columns) - column_names_by_table[qualified]):
                warnings.append(f"annotation references unknown column {qualified}.{unknown}")
            details = tuple(
                ColumnDescriptor(
                    name=c.name,
                    data_type=c.data_type,
                    nullable=c.nullable,
                    key_role="pk" if c.name in pk_names else "fk" if c.name in fk_columns else "none",
                    description=(ann_columns.get(c.name) or {}).get("description"),
                )
                for c in cols
            )
            pks = pk_by_table.get((schema, table), ())
            tables_content.append({
                "name": qualified,
                "columns": [c.name for c in cols],
                "primary_key": pks[0].column_name if pks else None,
                "column_details": [_column_to_dict(d) for d in details],
                "description": ann_table.get("description"),
                "foreign_keys": [],  # filled after relationships are built
            })
        for unknown_table in sorted(set(ann_tables) - set(column_names_by_table)):
            warnings.append(f"annotation references unknown table {unknown_table}")

        # --- relationships ---------------------------------------------
        relationships: list[dict] = []
        seen_pairs: set[tuple[str, str, str, str]] = set()
        for fk in metadata.foreign_keys:
            rel = {
                "from_table": f"{fk.from_schema}.{fk.from_table}",
                "from_column": fk.from_column,
                "to_table": f"{fk.to_schema}.{fk.to_table}",
                "to_column": fk.to_column,
                "name": fk.constraint_name,
                "kind": "database_constraint",
                "cardinality": None,
            }
            relationships.append(rel)
            seen_pairs.add((rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"]))
        for rel_ann in ann.get("relationships", []) or []:
            source = (
                rel_ann.get("from_table"), rel_ann.get("from_column"),
                rel_ann.get("to_table"), rel_ann.get("to_column"),
            )
            if not all(isinstance(part, str) and part for part in source):
                warnings.append("relationship annotation is incomplete")
                continue
            from_table, from_column, to_table, to_column = source
            if from_table not in column_names_by_table or to_table not in column_names_by_table:
                warnings.append(f"relationship annotation references unknown table ({from_table} -> {to_table})")
                continue
            if from_column not in column_names_by_table[from_table] or to_column not in column_names_by_table[to_table]:
                warnings.append(
                    f"relationship annotation references unknown column ({from_table}.{from_column} -> {to_table}.{to_column})"
                )
                continue
            if source in seen_pairs:
                continue  # a declared constraint already covers this pair
            relationships.append({
                "from_table": from_table,
                "from_column": from_column,
                "to_table": to_table,
                "to_column": to_column,
                "name": rel_ann.get("name"),
                "kind": "annotated",
                "cardinality": rel_ann.get("cardinality"),
            })
        relationships.sort(key=lambda r: (r["from_table"], r["from_column"], r["to_table"], r["to_column"]))
        rel_by_from: dict[str, list[dict]] = {}
        for rel in relationships:
            rel_by_from.setdefault(rel["from_table"], []).append(rel)
        for table in tables_content:
            table["foreign_keys"] = list(rel_by_from.get(table["name"], []))

        # --- business semantics ------------------------------------------
        known_columns = {
            f"{t}.{c}" for t, names in column_names_by_table.items() for c in names
        }
        semantics: list[dict] = []
        for sem in ann.get("business_semantics", []) or []:
            applies_to = [t for t in (sem.get("applies_to") or []) if t in known_columns]
            dropped = len(list(sem.get("applies_to") or [])) - len(applies_to)
            if dropped:
                warnings.append(f"business semantics annotation references unknown columns (count={dropped})")
            if not applies_to:
                continue
            semantics.append({
                "description": sem.get("description"),
                "formula": sem.get("formula"),
                "applies_to": sorted(applies_to),
            })
        semantics.sort(key=lambda s: (tuple(s["applies_to"]), s["description"] or ""))

        # --- labels --------------------------------------------------------
        raw_labels = ann.get("labels", {}) or {}
        labels = {}
        for key in sorted(raw_labels):
            table, _, column = key.rpartition(".")
            if table in column_names_by_table and column in column_names_by_table[table]:
                labels[key] = raw_labels[key]
            else:
                warnings.append(f"label annotation references unknown column {key}")

        content = {
            "tables": tables_content,
            "relationships": relationships,
            "business_semantics": semantics,
            "labels": labels,
        }
        if warnings:
            log_event(
                "schema_annotation_drift",
                level=logging.WARNING,
                component="schema",
                warning_count=len(warnings),
            )
        return content, tuple(warnings)

    # ------------------------------------------------------------------
    # Annotations
    # ------------------------------------------------------------------

    def _load_annotations(self) -> None:
        self._annotations, self._annotations_hash = self._read_annotations()

    def _reload_annotations_if_changed(self) -> None:
        """Re-check the annotations file at refresh time.

        Annotations are user-authored and may be edited between refreshes;
        the file hash participates in the probe signature, so an edited
        file forces full rediscovery and a content-based new version. A
        file that becomes unreadable degrades to structural-only
        operation with a recorded warning (same as at construction).
        """
        annotations, annotations_hash = self._read_annotations()
        if annotations_hash != self._annotations_hash:
            self._annotations = annotations
            self._annotations_hash = annotations_hash

    def _read_annotations(self) -> tuple[dict, Optional[str]]:
        try:
            with open(self._annotations_path, "r", encoding="utf-8") as handle:
                annotations = json.load(handle)
            annotations_hash = hashlib.sha256(
                json.dumps(annotations, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            return annotations, annotations_hash
        except (OSError, ValueError) as exc:
            # Structural-only operation with a recorded warning; the
            # snapshot is still trustworthy for structural truth.
            log_event(
                "schema_annotation_load_failed",
                level=logging.WARNING,
                component="schema",
                error_type=type(exc).__name__,
            )
            return {}, None

    # ------------------------------------------------------------------
    # Cache / history
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        try:
            with open(self._cache_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if raw.get("cache_version") != _CACHE_VERSION or not isinstance(raw.get("snapshots"), list):
                raise ValueError("unsupported cache structure")
            for entry in raw["snapshots"]:
                version = SchemaVersion(
                    id=str(entry["version"]["id"]),
                    captured_at=datetime.fromisoformat(entry["version"]["captured_at"]),
                    source=str(entry["version"]["source"]),
                )
                content = entry["content"]
                self._register(_snapshot_from_content(version, content), content)
            self._current_id = raw.get("current") if raw.get("current") in self._snapshots else None
            self._probe = raw.get("probe") if isinstance(raw.get("probe"), dict) else None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # Corruption or absence → treated as cache miss; rebuilt on
            # the next refresh (documented behavior).
            self._snapshots.clear()
            self._contents.clear()
            self._order.clear()
            self._current_id = None
            self._probe = None
            if not isinstance(exc, FileNotFoundError):
                log_event(
                    "schema_cache_load_failed",
                    level=logging.WARNING,
                    component="schema",
                    error_type=type(exc).__name__,
                )

    def _register(self, snapshot: SchemaSnapshot, content: dict) -> None:
        if snapshot.version.id in self._snapshots:
            return
        self._snapshots[snapshot.version.id] = snapshot
        self._contents[snapshot.version.id] = content
        self._order.append(snapshot.version.id)
        self._current_id = snapshot.version.id
        self._prune()

    def _prune(self) -> None:
        while len(self._order) > self._history_size:
            oldest = self._order.pop(0)
            self._snapshots.pop(oldest, None)
            self._contents.pop(oldest, None)

    def _save_cache(self) -> None:
        payload = {
            "cache_version": _CACHE_VERSION,
            "probe": self._probe,
            "current": self._current_id,
            "snapshots": [
                {
                    "version": {
                        "id": v.version.id,
                        "captured_at": v.version.captured_at.isoformat(),
                        "source": v.version.source,
                    },
                    "content": self._contents[v.version.id],
                }
                for v in (self._snapshots[i] for i in self._order)
            ],
        }
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except OSError as exc:
            # Cache write failure never invalidates the in-memory snapshot.
            log_event(
                "schema_cache_write_failed",
                level=logging.WARNING,
                component="schema",
                error_type=type(exc).__name__,
            )


# ----------------------------------------------------------------------
# Canonical serialization helpers (deterministic; fingerprint input)
# ----------------------------------------------------------------------


def _column_to_dict(column: ColumnDescriptor) -> dict:
    return {
        "name": column.name,
        "data_type": column.data_type,
        "nullable": bool(column.nullable),
        "key_role": column.key_role,
        "description": column.description,
    }


def _relationship_to_dict(relationship: Mapping[str, Any]) -> dict:
    return {
        "from_table": relationship["from_table"],
        "from_column": relationship["from_column"],
        "to_table": relationship["to_table"],
        "to_column": relationship["to_column"],
        "name": relationship["name"],
        "kind": relationship["kind"],
        "cardinality": relationship["cardinality"],
    }


def _semantics_to_dict(semantics: Mapping[str, Any]) -> dict:
    return {
        "description": semantics["description"],
        "formula": semantics["formula"],
        "applies_to": list(semantics["applies_to"]),
    }


def _fingerprint(content: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical serialization of snapshot content.

    Excludes the version block entirely (``captured_at``, ``source``):
    identity is pure structural + annotated content, stable across
    dictionary order, driver row order, process and host.
    """
    payload = {
        "tables": [
            {
                "name": t["name"],
                "columns": list(t["columns"]),
                "primary_key": t["primary_key"],
                "column_details": [_column_to_dict(ColumnDescriptor(**d)) for d in t["column_details"]],
                "description": t["description"],
                "foreign_keys": [_relationship_to_dict(r) for r in t["foreign_keys"]],
            }
            for t in content["tables"]
        ],
        "relationships": [_relationship_to_dict(r) for r in content["relationships"]],
        "business_semantics": [_semantics_to_dict(s) for s in content["business_semantics"]],
        "labels": dict(sorted(content["labels"].items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _snapshot_from_content(version: SchemaVersion, content: Mapping[str, Any]) -> SchemaSnapshot:
    """Rebuild the immutable contract snapshot from canonical content."""
    tables = tuple(
        TableDescriptor(
            name=t["name"],
            columns=tuple(t["columns"]),
            primary_key=t["primary_key"],
            foreign_keys=tuple(
                Relationship(
                    from_table=r["from_table"],
                    from_column=r["from_column"],
                    to_table=r["to_table"],
                    to_column=r["to_column"],
                    name=r["name"],
                    kind=r["kind"],
                    cardinality=r["cardinality"],
                )
                for r in t["foreign_keys"]
            ),
            column_details=tuple(ColumnDescriptor(**d) for d in t["column_details"]),
            description=t["description"],
        )
        for t in content["tables"]
    )
    relationships = tuple(
        Relationship(
            from_table=r["from_table"],
            from_column=r["from_column"],
            to_table=r["to_table"],
            to_column=r["to_column"],
            name=r["name"],
            kind=r["kind"],
            cardinality=r["cardinality"],
        )
        for r in content["relationships"]
    )
    semantics = tuple(
        BusinessSemantics(
            description=s["description"],
            formula=s["formula"],
            applies_to=tuple(s["applies_to"]),
        )
        for s in content["business_semantics"]
    )
    return SchemaSnapshot(
        version=version,
        tables=tables,
        relationships=relationships,
        business_semantics=semantics,
        labels=dict(content["labels"]),
    )
