"""Database execution boundary public API.

Exports are lazy (PEP 562): importing ``src.core.database.policy`` (the
driver-free value objects) must not load ``connection.py`` and therefore
not import the ``pymssql`` driver. See ADR-016 and
``tests/test_architecture/test_import_footprint.py``.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static-analysis aid only
    from src.core.database.connection import DatabaseConnection
    from src.core.database.policy import DatabaseError, DatabaseErrorCode, ExecutionPolicy

_EXPORTS = {
    "DatabaseConnection": "src.core.database.connection",
    "DatabaseError": "src.core.database.policy",
    "DatabaseErrorCode": "src.core.database.policy",
    "ExecutionPolicy": "src.core.database.policy",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
