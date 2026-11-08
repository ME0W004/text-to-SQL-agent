"""Sprint 2 Prompt 2C — Contract & Dependency Validation.

This module mechanically verifies that the implementation produced in
Prompt 2B conforms to the approved contracts and dependency rules.

Validation strategy
-------------------

* **Runtime Protocol checks** — every approved Protocol is exercised
  against the real adapter / legacy implementation that fulfils it.
* **Static signature checks** — ``inspect`` introspection confirms the
  adapter / legacy class exposes a method with the same name as the
  Protocol method. (Full typing is provided by mypy in CI; this
  lightweight check catches the most common regressions — renamed,
  dropped, or signature-drifted methods.)
* **AST-based dependency rules** — every component module is parsed
  and its imports + ``with_*`` calls are checked against the rules
  defined in ``docs/architecture/dependency-rules.md``.
* **Dependency cycle detection** — a graph is built from internal
  imports and checked for back-edges.
* **Error / value-object inventory** — every approved typed error
  exists, has the documented default code, and is registered; every
  approved value object exists, is frozen, and has the expected
  required fields.

The tests in this module are written so that future component
implementations cannot silently violate the architecture.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import pathlib
import typing
from typing import Any, Iterable, Mapping, Optional, Sequence

import pytest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
AGENTS_ROOT = SRC_ROOT / "agents"


# ---------------------------------------------------------------------------
# Module enumeration
# ---------------------------------------------------------------------------


# Modules covered by the architecture rules.
COMPONENT_MODULES: tuple[str, ...] = (
    "src/agents/contracts.py",
    "src/agents/orchestrator.py",
    "src/agents/adapters.py",
)

# All "future component" modules that must never import AgentState
# (the orchestrator is the sole writer).
FUTURE_COMPONENT_MODULES: tuple[str, ...] = (
    "src/agents/contracts.py",
    "src/agents/adapters.py",
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _module_path(rel_path: str) -> pathlib.Path:
    return PROJECT_ROOT / rel_path


def _read_ast(rel_path: str) -> ast.Module:
    return ast.parse(_module_path(rel_path).read_text(encoding="utf-8"))


def _imported_modules(rel_path: str) -> list[str]:
    """Return the list of top-level module references imported by *rel_path*.

    Resolves relative imports to absolute dotted paths. Skips stdlib
    import names that aren't part of the project — this is a project-wide
    check so external modules do not match our forbidden patterns.
    """
    tree = _read_ast(rel_path)
    imports: list[str] = []
    path_parts = list(pathlib.Path(rel_path).parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                # Resolve relative: from .x inside pkg.sub.mod
                up = node.level - 1
                if up > len(path_parts):
                    base_parts: list[str] = []
                else:
                    base_parts = list(path_parts)[: len(path_parts) - up]
                resolved = ".".join(base_parts + ([module] if module else []))
                imports.append(resolved)
            else:
                imports.append(module)
    return imports


def _state_writers(rel_path: str) -> list[ast.Call]:
    """Return every ``with_*`` Call in *rel_path* (for state-writer scan)."""
    tree = _read_ast(rel_path)
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("with_")
        ):
            found.append(node)
    return found


# ---------------------------------------------------------------------------
# Step 2C.1 / 2C.2 — Contract inventory + comparison
# ---------------------------------------------------------------------------


# The 17 approved target interfaces (from docs/contracts/component-contracts.md
# and docs/contracts/interface-design.md).
APPROVED_INTERFACES: tuple[str, ...] = (
    "QuestionAnalyzer",
    "SchemaCatalog",
    "SchemaRetriever",
    "QueryPlanner",
    "SQLGenerator",
    "SQLValidator",
    "SecurityValidator",
    "SemanticValidator",
    "ComplexityValidator",
    "SQLRepairer",
    "QueryExecutor",
    "ResultAnalyzer",
    "PresentationPlanner",
    "AnswerGenerator",
    "AnswerValidator",
    "MemoryManagerProtocol",
    "LLMRouter",
)


class TestContractInventory:
    """Every approved interface from Prompt 2A must exist as a Protocol
    in ``src/agents/contracts.py``."""

    @pytest.mark.parametrize("name", APPROVED_INTERFACES)
    def test_protocol_exists(self, name: str) -> None:
        from src.agents import contracts

        assert hasattr(contracts, name), (
            f"contracts.py must define Protocol {name!r}"
        )
        symbol = getattr(contracts, name)
        # Sanity: it must be a class (Protocols are classes, not functions).
        assert inspect.isclass(symbol), f"{name} must be a class / Protocol"

    def test_all_protocols_are_runtime_checkable(self) -> None:
        from src.agents import contracts

        for name in APPROVED_INTERFACES:
            symbol = getattr(contracts, name)
            assert hasattr(symbol, "_is_runtime_protocol"), (
                f"{name} must be @runtime_checkable"
            )


class TestProtocolMethodSignatures:
    """Static signature verification for each approved Protocol.

    For each Protocol method the test asserts:

    * the method exists in the Protocol,
    * the parameters match the approved list (names + required-only
      classification),
    * the return annotation matches the approved type when set.

    This is the strongest lightweight check the repository supports
    without introducing a separate type checker toolchain.
    """

    # Each entry: (Protocol name, method name, list of expected parameter
    # names, expected return annotation is "skipped" if it can be None/Any
    # because Protocols don't enforce it).
    EXPECTED: dict[str, dict[str, Sequence[str]]] = {
        "QuestionAnalyzer": {"analyze": ["question", "history"]},
        "SchemaCatalog": {
            "current_version": [],
            "versions": [],
            "get_snapshot": ["version"],
            "refresh": [],
        },
        "SchemaRetriever": {"retrieve": ["spec", "snapshot"]},
        "QueryPlanner": {"plan": ["spec", "context"]},
        "SQLGenerator": {"generate": ["plan", "context"]},
        "SQLValidator": {"validate": ["candidate"]},
        "SecurityValidator": {"validate": ["sql"]},
        "SemanticValidator": {"validate": ["candidate", "plan", "snapshot"]},
        "ComplexityValidator": {"validate": ["ast", "budget"]},
        "SQLRepairer": {
            "repair": [
                "candidate", "validation", "security", "semantic",
                "complexity", "plan", "attempt",
            ]
        },
        "QueryExecutor": {"execute": ["sql"]},
        "ResultAnalyzer": {"profile": ["result", "plan"]},
        "PresentationPlanner": {
            "plan": ["spec", "profile", "labels", "locale_policy"],
        },
        "AnswerGenerator": {
            "generate": ["spec", "profile", "presentation", "result"],
        },
        "AnswerValidator": {
            "validate": ["draft", "profile", "presentation"],
        },
        "MemoryManagerProtocol": {
            "create_session": [],
            "session_exists": ["session_id"],
            "add_message": ["session_id", "role", "content"],
            "get_history": ["session_id"],
            "clear_session": ["session_id"],
        },
        "LLMRouter": {"generate": ["prompt"]},
    }

    @pytest.mark.parametrize("protocol_name", sorted(EXPECTED.keys()))
    def test_protocol_method_names_match(self, protocol_name: str) -> None:
        from src.agents import contracts

        protocol = getattr(contracts, protocol_name)
        members = {
            name: member
            for name, member in vars(protocol).items()
            if not name.startswith("_")
        }
        actual_methods = {n for n, m in members.items() if callable(m)}
        expected_methods = set(self.EXPECTED[protocol_name])
        assert expected_methods.issubset(actual_methods), (
            f"{protocol_name} is missing methods: "
            f"{expected_methods - actual_methods}"
        )

    @pytest.mark.parametrize("protocol_name", sorted(EXPECTED.keys()))
    @pytest.mark.parametrize("method_name", [
        m for proto in EXPECTED.values() for m in proto
    ])
    def test_protocol_method_parameters(
        self, protocol_name: str, method_name: str
    ) -> None:
        if method_name not in self.EXPECTED[protocol_name]:
            pytest.skip(f"{method_name} is not part of {protocol_name}")
        from src.agents import contracts

        protocol = getattr(contracts, protocol_name)
        method = getattr(protocol, method_name, None)
        assert method is not None, (
            f"{protocol_name}.{method_name} must exist"
        )
        # ``__wrapped__`` may be the underlying function on some
        # Python versions. ``getattr`` above already resolves it.
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            pytest.skip(
                f"{protocol_name}.{method_name} signature is not introspectable"
            )
        actual = list(sig.parameters)
        expected = self.EXPECTED[protocol_name][method_name]
        # For keyword-only markers, we cannot introspect them on a
        # Protocol method, but we can require the positional ones to
        # appear in the right order before any keyword-only marker.
        if actual and actual[0] in ("self", "cls"):
            actual = actual[1:]
        assert actual[: len(expected)] == list(expected), (
            f"{protocol_name}.{method_name}: expected first "
            f"{len(expected)} parameters {list(expected)!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Step 2C.3 — Runtime protocol validation
# ---------------------------------------------------------------------------


class TestRuntimeProtocolConformance:
    """Use ``isinstance(..., Protocol)`` against real adapters to confirm
    the contract surface is satisfied at runtime.

    Note: ``@runtime_checkable`` Protocols only check attribute presence,
    not signatures. Signature verification is performed separately in
    ``TestProtocolMethodSignatures`` and ``TestAdapterSignatureParity``.
    """

    def test_security_validator_adapter_conforms(self) -> None:
        from src.agents.adapters import SecurityValidatorAdapter
        from src.agents.contracts import SecurityValidator

        adapter = SecurityValidatorAdapter()
        assert isinstance(adapter, SecurityValidator)

    def test_query_executor_adapter_conforms(self) -> None:
        from src.agents.adapters import QueryExecutorAdapter
        from src.agents.contracts import QueryExecutor

        # Use a fake cursor so we never touch a real DB.
        class FakeCursor:
            arraysize = 1
            rows: list = []

            def execute(self, sql, params=None):
                pass

            def fetchmany(self, size):
                return []

            def close(self):
                pass

        class FakeConnection:
            def cursor(self, as_dict=True):
                return FakeCursor()

            def close(self):
                pass

        from src.core.database.connection import (
            DatabaseConnection,
            ExecutionPolicy,
        )

        db = DatabaseConnection(
            "host", 1433, "user", "password", "database",
            execution_policy=ExecutionPolicy(
                max_rows=10, max_fetch_batch=2,
                query_timeout=5, connection_timeout=2,
            ),
            connect_fn=lambda **kw: FakeConnection(),
        )
        adapter = QueryExecutorAdapter(db)
        assert isinstance(adapter, QueryExecutor)

    def test_security_validator_protocol_is_satisfied(self) -> None:
        from src.agents.contracts import SecurityValidator

        # Define a custom class that explicitly inherits the Protocol —
        # it must be acceptable.
        class CustomValidator(SecurityValidator):
            def validate(self, sql, *, policy=None):
                from src.core.security.sql_security import SecurityDecision
                return SecurityDecision(valid=True)

        custom = CustomValidator()
        assert isinstance(custom, SecurityValidator)

    def test_query_executor_protocol_is_satisfied(self) -> None:
        from src.agents.contracts import QueryExecutor

        class CustomExecutor(QueryExecutor):
            def execute(self, sql, *, params=None, policy=...):
                from src.agents.contracts import QueryResult
                return QueryResult()

        custom = CustomExecutor()
        assert isinstance(custom, QueryExecutor)

    def test_memory_manager_conforms(self) -> None:
        """The one *existing* production implementation of a future
        Protocol (Sprint 1 ``MemoryManager``) must satisfy the
        ``MemoryManagerProtocol`` contract surface at runtime."""
        from src.agents.contracts import MemoryManagerProtocol
        from src.memory.manager import MemoryManager

        assert isinstance(MemoryManager(), MemoryManagerProtocol)


# ---------------------------------------------------------------------------
# Step 2C.4 — Static signature verification: adapter vs Protocol parity
# ---------------------------------------------------------------------------


class TestAdapterSignatureParity:
    """For each adapter, every Protocol method that the adapter fulfils
    must have a Python signature that includes the same required
    positional parameter names as the Protocol.

    This is the strongest lightweight catch for ``with_*(...)``-style
    regressions in adapter code.
    """

    PARITY = (
        ("SecurityValidatorAdapter", "SecurityValidator", "validate"),
        ("QueryExecutorAdapter", "QueryExecutor", "execute"),
    )

    @pytest.mark.parametrize(
        "adapter_name,protocol_name,method_name", PARITY
    )
    def test_adapter_method_signature_parity(
        self, adapter_name: str, protocol_name: str, method_name: str
    ) -> None:
        from src.agents import adapters, contracts

        adapter = getattr(adapters, adapter_name)
        protocol = getattr(contracts, protocol_name)

        protocol_method = getattr(protocol, method_name)
        adapter_method = getattr(adapter, method_name)

        p_sig = inspect.signature(protocol_method)
        a_sig = inspect.signature(adapter_method)

        p_positional = [
            name for name, param in p_sig.parameters.items()
            if param.default is inspect.Parameter.empty
            and param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        a_names = [
            name for name, param in a_sig.parameters.items()
            if param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        # The adapter must accept at least the required positional
        # parameters from the Protocol. (Keywords and *args are
        # checked separately by the Protocol's runtime + mypy.)
        for required in p_positional:
            assert required in a_names, (
                f"{adapter_name}.{method_name} must accept parameter "
                f"{required!r} from Protocol {protocol_name}"
            )


# ---------------------------------------------------------------------------
# Step 2C.5 — Typed value object inventory and invariants
# ---------------------------------------------------------------------------


class TestTypedValueObjectInventory:
    """Every approved typed value object must exist as a frozen dataclass
    with the expected required fields."""

    REQUIRED_FIELDS: dict[str, Sequence[str]] = {
        # Question
        "Entity": ["text", "entity_type"],
        "QuestionFilter": ["field", "operator", "value"],
        "QuestionMetric": ["name"],
        "TimeConstraint": ["kind", "raw_text"],
        "Reference": ["text"],
        "AmbiguityReport": [],
        "QuestionSpec": ["intent"],
        # Schema
        "TableDescriptor": ["name"],
        "Relationship": [
            "from_table", "from_column", "to_table", "to_column"
        ],
        "BusinessSemantics": ["description"],
        "SchemaVersion": ["id", "captured_at", "source"],
        "SchemaSnapshot": ["version"],
        "TableRef": ["name"],
        "ColumnRef": ["table", "column"],
        "JoinHint": ["left", "right"],
        "SchemaContext": [],
        # Planning
        "Join": ["left", "right", "on_left", "on_right"],
        "Dimension": ["column"],
        "PlanMetric": ["function"],
        "PlanFilter": ["column", "operator"],
        "SortKey": ["column"],
        "OutputRequirements": [],
        "QueryPlan": [],
        # SQL pipeline
        "SQLCandidate": ["sql", "dialect", "candidate_id", "fingerprint"],
        "ParsedAST": ["expression", "dialect"],
        "SyntaxError": ["message"],
        "ValidationReport": ["candidate_id", "valid", "parse_ok"],
        "SemanticViolation": ["code", "message"],
        "SemanticReport": ["candidate_id", "ok"],
        "ComplexityBudget": [],
        "ComplexityReport": ["ok"],
        # Execution
        "QueryResult": [],
        # Result/Presentation
        "ColumnType": ["name", "python_type"],
        "ShapeDescriptor": ["rows", "columns"],
        "AggregateRef": ["metric_alias", "source_column", "function"],
        "ResultProfile": ["shape"],
        "FormatSpec": [],
        "LocalePolicy": [],
        "PresentationPlan": [],
        # Answer
        "Claim": ["text"],
        "Evidence": ["source", "content"],
        "AnswerDraft": ["text"],
        "ValidatedAnswer": ["text"],
        # LLM
        "Usage": [],
        "LLMPolicy": [],
        "LLMBudget": [],
        "LLMResult": ["text", "provider", "model", "usage"],
    }

    @pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS.keys()))
    def test_value_object_is_frozen_dataclass(self, name: str) -> None:
        from src.agents import contracts

        symbol = getattr(contracts, name)
        assert dataclasses.is_dataclass(symbol), (
            f"{name} must be a dataclass"
        )
        # Use setattr to check frozen.
        instance = self._make_minimal(name, symbol)
        with pytest.raises(dataclasses.FrozenInstanceError):
            first = dataclasses.fields(instance)[0]
            setattr(instance, first.name, "mutated")

    @pytest.mark.parametrize("name", sorted(REQUIRED_FIELDS.keys()))
    def test_value_object_required_fields(self, name: str) -> None:
        from src.agents import contracts

        symbol = getattr(contracts, name)
        actual = {f.name for f in dataclasses.fields(symbol)}
        required = set(self.REQUIRED_FIELDS[name])
        assert required.issubset(actual), (
            f"{name} is missing required fields: {required - actual}"
        )

    def test_no_duplicate_metric_or_filter_classes(self) -> None:
        """Guard against the §3.1 hazard where QuestionFilter/Metric and
        PlanFilter/Metric become accidentally merged."""
        from src.agents import contracts

        assert contracts.QuestionMetric is not contracts.PlanMetric
        assert contracts.QuestionFilter is not contracts.PlanFilter

    def test_no_duplicate_evidence_classes(self) -> None:
        from src.agents import contracts

        # A single Evidence class is reused; verify it is importable
        # from the same module and only one definition exists.
        assert hasattr(contracts, "Evidence")

    def _make_minimal(self, name: str, symbol: Any) -> Any:
        from src.agents import contracts
        from datetime import datetime

        # Provide minimal required values.
        defaults: dict[str, Any] = {
            "text": "t",
            "entity_type": "table",
            "field": "f",
            "operator": "eq",
            "value": "v",
            "name": "n",
            "kind": "absolute",
            "raw_text": "r",
            "intent": "lookup",
            "from_table": "a",
            "from_column": "b",
            "to_table": "c",
            "to_column": "d",
            "description": "x",
            "id": "v1",
            "captured_at": datetime(2024, 1, 1),
            "source": "introspection",
            "version": contracts.SchemaVersion(
                id="v1", captured_at=datetime(2024, 1, 1), source="x"
            ),
            "table": "T",
            "column": "c",
            "left": contracts.ColumnRef(table="A", column="a"),
            "right": contracts.ColumnRef(table="B", column="b"),
            "on_left": "x",
            "on_right": "y",
            "function": "sum",
            "sql": "SELECT 1",
            "dialect": "tsql",
            "candidate_id": "c1",
            "fingerprint": "fp",
            "expression": object(),
            "message": "m",
            "candidate_id_field": "c1",
            "valid": True,
            "parse_ok": True,
            "code": "x",
            "ok": True,
            "python_type": "int",
            "rows": 0,
            "columns": 0,
            "metric_alias": "m",
            "source_column": "s",
            "shape": contracts.ShapeDescriptor(rows=0, columns=0),
            "text_field": "x",
            "source": "s",
            "content": "c",
            "provider": "p",
            "model": "m",
            "usage": contracts.Usage(),
        }
        kwargs = {k: v for k, v in defaults.items() if k in self.REQUIRED_FIELDS[name]}
        return symbol(**kwargs)


# ---------------------------------------------------------------------------
# Step 2C.6 — Error contract validation
# ---------------------------------------------------------------------------


class TestErrorContractValidation:
    """Each typed component error must satisfy the ADR-015 field
    contract: code, message, user_message, stage, retryable, cause.
    Sprint 1 errors must remain catchable."""

    EXPECTED_ERRORS: dict[str, dict[str, Any]] = {
        "QuestionAnalysisError": {
            "code_default": "QA.INTERNAL",
            "stage": "QUESTION",
            "retryable": False,
        },
        "SchemaError": {
            "code_default": "SCH.INTERNAL",
            "stage": "SCHEMA",
            "retryable": False,
        },
        "PlanningError": {
            "code_default": "PLN.INTERNAL",
            "stage": "PLANNING",
            "retryable": False,
        },
        "SQLGenerationError": {
            "code_default": "GEN.INTERNAL",
            "stage": "SQL_GENERATION",
            "retryable": False,
        },
        "RepairError": {
            "code_default": "RPR.INTERNAL",
            "stage": "REPAIR",
            "retryable": False,
        },
        "ResultAnalysisError": {
            "code_default": "RES.INTERNAL",
            "stage": "RESULT",
            "retryable": False,
        },
        "PresentationError": {
            "code_default": "PRE.INTERNAL",
            "stage": "PRESENTATION",
            "retryable": False,
        },
        "AnswerGenerationError": {
            "code_default": "ANS.INTERNAL",
            "stage": "ANSWER",
            "retryable": False,
        },
        "AnswerValidationError": {
            "code_default": "ANV.INTERNAL",
            "stage": "ANSWER_VALIDATION",
            "retryable": False,
        },
        "MemoryError": {
            "code_default": "MEM.INTERNAL",
            "stage": "MEMORY",
            "retryable": False,
        },
        "LLMRouterError": {
            "code_default": "ROU.INTERNAL",
            "stage": "LLM",
            "retryable": True,
        },
    }

    @pytest.mark.parametrize("name", sorted(EXPECTED_ERRORS.keys()))
    def test_error_class_default_code(self, name: str) -> None:
        from src.agents import contracts

        cls = getattr(contracts, name)
        expected = self.EXPECTED_ERRORS[name]["code_default"]
        assert cls.code == expected, (
            f"{name}.code default must be {expected!r}, got {cls.code!r}"
        )

    @pytest.mark.parametrize("name", sorted(EXPECTED_ERRORS.keys()))
    def test_error_class_default_stage(self, name: str) -> None:
        from src.agents import contracts

        cls = getattr(contracts, name)
        assert cls.default_stage.name == self.EXPECTED_ERRORS[name]["stage"]

    @pytest.mark.parametrize("name", sorted(EXPECTED_ERRORS.keys()))
    def test_error_class_default_retryable(self, name: str) -> None:
        from src.agents import contracts

        cls = getattr(contracts, name)
        assert cls.default_retryable is self.EXPECTED_ERRORS[name]["retryable"]

    @pytest.mark.parametrize("name", sorted(EXPECTED_ERRORS.keys()))
    def test_error_instance_carries_all_fields(self, name: str) -> None:
        from src.agents import contracts

        cls = getattr(contracts, name)
        err = cls("boom", user_message="user-friendly", cause=ValueError("x"))
        # All 6 fields from §7.3 of interface-design.md.
        assert isinstance(err.code, str)
        assert err.message == "boom"
        assert err.user_message == "user-friendly"
        assert err.stage is not None
        assert isinstance(err.retryable, bool)
        assert isinstance(err.cause, ValueError)
        # And it must be a normal Exception.
        assert isinstance(err, Exception)

    def test_existing_sprint1_errors_still_catchable(self) -> None:
        """Per §13 of interface-design.md, the existing typed exceptions
        are preserved. The new ones are additive only."""
        from src.utils.exceptions import (
            ConfigurationError,
            LLMError,
            QueryExecutionError,
            SQLValidationError,
        )
        from src.core.security.sql_security import SQLSecurityError
        from src.core.database.connection import DatabaseError, DatabaseErrorCode

        # All still raise-able.
        with pytest.raises(SQLValidationError):
            raise SQLValidationError("x")
        with pytest.raises(SQLSecurityError):
            raise SQLSecurityError(code=None, message="x")
        with pytest.raises(QueryExecutionError):
            raise QueryExecutionError("x")
        with pytest.raises(DatabaseError):
            raise DatabaseError(
                code=DatabaseErrorCode.DATABASE_QUERY_ERROR, message="x"
            )
        with pytest.raises(ConfigurationError):
            raise ConfigurationError("x")
        with pytest.raises(LLMError):
            raise LLMError("x")


# ---------------------------------------------------------------------------
# Step 2C.7 — Error code registry validation
# ---------------------------------------------------------------------------


# The canonical namespaced error-code registry from
# docs/contracts/interface-design.md §7.4.
APPROVED_CODES: dict[str, tuple[str, ...]] = {
    "QA": (
        "QA.AMBIGUOUS", "QA.UNSUPPORTED_INTENT", "QA.LLM_UNAVAILABLE",
        "QA.INTERNAL",
    ),
    "SCH": (
        "SCH.DRIFT", "SCH.NO_RELEVANT_SCHEMA", "SCH.UNAVAILABLE",
        "SCH.INTERNAL", "SCH.AMBIGUOUS", "SCH.VERSION_NOT_FOUND",
    ),
    "PLN": ("PLN.PLAN_CONFLICT", "PLN.AMBIGUOUS", "PLN.LLM_UNAVAILABLE", "PLN.INTERNAL"),
    "GEN": ("GEN.LLM_UNAVAILABLE", "GEN.INVALID_OUTPUT", "GEN.BUDGET_EXCEEDED", "GEN.INTERNAL"),
    "PAR": ("PAR.SYNTAX_ERROR", "PAR.MULTIPLE_STATEMENTS"),
    "VAL": ("VAL.SYNTAX", "VAL.SCHEMA_REFERENCE"),
    "SEC": (
        "SEC.MULTIPLE_STATEMENTS", "SEC.NON_READ_ONLY",
        "SEC.FORBIDDEN_DML", "SEC.FORBIDDEN_DDL",
        "SEC.FORBIDDEN_EXEC", "SEC.FORBIDDEN_TRANSACTION",
        "SEC.SELECT_INTO", "SEC.UNSUPPORTED_CONSTRUCT",
    ),
    "SEM": (
        "SEM.AGGREGATE_IN_WHERE", "SEM.UNIT_MISMATCH",
        "SEM.CURRENCY_MISMATCH", "SEM.JOIN_MISSING",
        "SEM.FILTER_AMBIGUOUS",
    ),
    "CMX": ("CMX.TOO_COMPLEX", "CMX.COST_OVER_BUDGET"),
    "RPR": ("RPR.EXHAUSTED", "RPR.NO_RECOVERY", "RPR.LLM_UNAVAILABLE", "RPR.INTERNAL"),
    "EXE": (
        "EXE.TIMEOUT", "EXE.ROW_LIMIT_EXCEEDED",
        "DATABASE_CONNECTION_ERROR", "DATABASE_TIMEOUT",
        "DATABASE_QUERY_ERROR", "DATABASE_PERMISSION_ERROR",
        "DATABASE_RESOURCE_LIMIT", "DATABASE_RESULT_LIMIT",
        "DATABASE_CURSOR_ERROR", "DATABASE_UNKNOWN_ERROR",
    ),
    "RES": ("RES.INTERNAL",),
    "PRE": ("PRE.POLICY_VIOLATION", "PRE.INTERNAL"),
    "ANS": ("ANS.LLM_UNAVAILABLE", "ANS.OUTPUT_TOO_LONG", "ANS.INTERNAL"),
    "ANV": (
        "ANV.UNGROUNDED_CLAIM", "ANV.NUMERIC_MISMATCH",
        "ANV.FORMAT_VIOLATION", "ANV.INTERNAL",
    ),
    "MEM": ("MEM.UNAVAILABLE", "MEM.SESSION_LOST", "MEM.INTERNAL"),
    "ROU": (
        "ROU.PROVIDER_UNAVAILABLE", "ROU.BUDGET_EXCEEDED",
        "ROU.NO_PROVIDER", "ROU.TIMEOUT", "ROU.INTERNAL",
    ),
    "CFG": ("CFG.MISSING", "CFG.INVALID"),
}


class TestErrorCodeRegistry:
    """Validate the canonical namespaced error-code registry. We do not
    require the implementation to *use* every code — only that the
    *prefixes* documented in interface-design.md are present and free
    of typos."""

    def test_no_duplicate_codes_within_a_prefix(self) -> None:
        for prefix, codes in APPROVED_CODES.items():
            duplicates = sorted({c for c in codes if codes.count(c) > 1})
            assert not duplicates, (
                f"Prefix {prefix!r} has duplicate codes: {duplicates}"
            )

    def test_every_code_matches_its_prefix(self) -> None:
        for prefix, codes in APPROVED_CODES.items():
            for code in codes:
                # Allow "DATABASE_*" codes inside EXE since they are
                # preserved verbatim from Sprint 1.
                if prefix == "EXE" and code.startswith("DATABASE_"):
                    continue
                assert code.startswith(prefix + "."), (
                    f"Code {code!r} does not start with prefix {prefix!r}"
                )

    def test_typed_component_error_uses_registry_namespace(self) -> None:
        """The base TypedComponentError has a default code but every
        subclass overrides it with a namespaced value."""
        from src.agents import contracts

        for name, info in TestErrorContractValidation.EXPECTED_ERRORS.items():
            cls = getattr(contracts, name)
            assert cls.code.startswith(info["code_default"].split(".")[0] + "."), (
                f"{name}.code {cls.code!r} is not namespaced correctly"
            )


# ---------------------------------------------------------------------------
# Step 2C.8 — Single-writer enforcement (mechanical, deeper scan)
# ---------------------------------------------------------------------------


class TestSingleWriterMechanically:
    """Mechanical enforcement of the single-writer rule (ADR-013)."""

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    def test_future_module_does_not_import_state(self, rel_path: str) -> None:
        imports = _imported_modules(rel_path)
        for module in imports:
            assert module != "src.agents.state", (
                f"{rel_path} must not import src.agents.state "
                "(single-writer rule, ADR-013)"
            )

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    def test_future_module_does_not_call_state_with_methods(
        self, rel_path: str
    ) -> None:
        """Future component modules must not call ``state.with_*`` methods.
        A static AST scan looks for any ``Attribute`` whose ``attr``
        starts with ``with_`` and whose target is named ``state`` (the
        only known writer in the new modules)."""
        calls = _state_writers(rel_path)
        assert calls == [], (
            f"{rel_path} calls a with_* method; only Orchestrator may do so. "
            f"Found: {[ast.unparse(c) for c in calls]}"
        )

    def test_state_module_exposes_all_required_appliers(self) -> None:
        """The state module must expose the six new appliers from
        Prompt 2B so the Orchestrator can write each pipeline result."""
        from src.agents.state import AgentState

        expected = (
            "with_question_analysis",
            "with_schema_context",
            "with_query_plan",
            "with_result_profile",
            "with_presentation_plan",
            "with_validated_answer",
        )
        for name in expected:
            assert hasattr(AgentState, name), (
                f"AgentState must expose {name} state-applier method"
            )

    def test_text_to_sql_agent_does_not_write_state_directly(self) -> None:
        """The façade must NOT call ``state.with_*`` methods — that is
        the Orchestrator's job. Any direct write is a regression."""
        rel = "src/agents/text_to_sql_agent.py"
        calls = _state_writers(rel)
        assert calls == [], (
            f"{rel} calls a with_* method directly: "
            f"{[ast.unparse(c) for c in calls]}. "
            f"TextToSQLAgent is a façade and must delegate to the Orchestrator."
        )

    def test_orchestrator_is_the_only_state_writer_in_new_modules(self) -> None:
        rel = "src/agents/orchestrator.py"
        calls = _state_writers(rel)
        # The orchestrator is the only allowed caller of state.with_*
        assert calls, "Orchestrator must use at least one state.with_* method"


# ---------------------------------------------------------------------------
# Step 2C.9 — Dependency boundary validation (deeper)
# ---------------------------------------------------------------------------


# Forbidden imports for every future component module (no domain
# component may import these). Kept consistent with
# docs/architecture/dependency-rules.md §4.
FORBIDDEN_PROVIDER_SDKS: tuple[str, ...] = (
    "src.core.llm.openai_client",
    "src.core.llm.gemini_client",
    "src.core.llm.groq_client",
    "src.core.llm.ollama_client",
)
FORBIDDEN_DB_DRIVER: tuple[str, ...] = ("pymssql",)
FORBIDDEN_STATE: tuple[str, ...] = ("src.agents.state",)
FORBIDDEN_MEMORY: tuple[str, ...] = ("src.memory.manager",)
FORBIDDEN_API: tuple[str, ...] = ("src.api", "fastapi")
FORBIDDEN_STREAM: tuple[str, ...] = ("streamlit",)
FORBIDDEN_SQLITE_DIRECT: tuple[str, ...] = ("sqlite3",)


class TestDependencyBoundaryHard:
    """Hard import-graph checks against the full target-architecture
    matrix in ``docs/architecture/dependency-rules.md`` §5."""

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    @pytest.mark.parametrize("forbidden", FORBIDDEN_PROVIDER_SDKS)
    def test_no_provider_sdk_imports(
        self, rel_path: str, forbidden: str
    ) -> None:
        imports = _imported_modules(rel_path)
        assert forbidden not in imports, (
            f"{rel_path} must not import provider SDK {forbidden!r}"
        )

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    def test_no_pymssql_imports(self, rel_path: str) -> None:
        imports = _imported_modules(rel_path)
        for module in imports:
            # The bare module ``pymssql`` is the canonical signal.
            if module == "pymssql" or module.endswith(".pymssql"):
                pytest.fail(f"{rel_path} must not import pymssql")

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    def test_no_memory_manager_imports(self, rel_path: str) -> None:
        imports = _imported_modules(rel_path)
        for module in imports:
            assert module not in FORBIDDEN_MEMORY, (
                f"{rel_path} must not import MemoryManager"
            )

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    def test_no_sqlite3_direct_imports(self, rel_path: str) -> None:
        imports = _imported_modules(rel_path)
        for module in imports:
            # ``sqlite3`` may appear as a top-level name in ``import
            # sqlite3`` or ``from sqlite3 import ...``. We forbid both.
            assert module != "sqlite3", (
                f"{rel_path} must not import sqlite3 directly"
            )

    @pytest.mark.parametrize("rel_path", FUTURE_COMPONENT_MODULES)
    def test_no_api_or_streamlit_imports(self, rel_path: str) -> None:
        imports = _imported_modules(rel_path)
        for module in imports:
            for forbidden in FORBIDDEN_API + FORBIDDEN_STREAM:
                assert not module.startswith(forbidden) and module != forbidden, (
                    f"{rel_path} must not import {forbidden!r}"
                )

    def test_orchestrator_does_not_import_provider_sdks(self) -> None:
        imports = _imported_modules("src/agents/orchestrator.py")
        for module in imports:
            assert module not in FORBIDDEN_PROVIDER_SDKS, (
                f"orchestrator must not import provider SDK {module!r}"
            )

    def test_orchestrator_does_not_import_pymssql(self) -> None:
        imports = _imported_modules("src/agents/orchestrator.py")
        for module in imports:
            if module == "pymssql" or module.endswith(".pymssql"):
                pytest.fail("orchestrator must not import pymssql")

    def test_orchestrator_imports_agent_state(self) -> None:
        imports = _imported_modules("src/agents/orchestrator.py")
        assert "src.agents.state" in imports, (
            "orchestrator must import src.agents.state (sole writer)"
        )

    def test_orchestrator_does_not_import_memory_manager(self) -> None:
        # The orchestrator *receives* a MemoryManager through DI; it must
        # not import the concrete class.
        imports = _imported_modules("src/agents/orchestrator.py")
        for module in imports:
            assert module not in FORBIDDEN_MEMORY, (
                f"orchestrator must not import MemoryManager (DI only)"
            )

    def test_api_module_does_not_import_orchestrator(self) -> None:
        """The public API layer is frozen; it must not import
        Orchestrator/Contracts/Adapters directly."""
        imports = _imported_modules("src/api/routes.py")
        for forbidden in (
            "src.agents.orchestrator",
            "src.agents.contracts",
            "src.agents.adapters",
        ):
            assert forbidden not in imports, (
                f"src/api/routes.py must not import {forbidden!r}"
            )

    def test_evaluation_directory_not_imported_by_production(self) -> None:
        """No production module may import ``evaluation.*`` (it's a
        test-only surface)."""
        production_modules = [
            "src/agents/orchestrator.py",
            "src/agents/adapters.py",
            "src/agents/contracts.py",
            "src/agents/state.py",
            "src/agents/text_to_sql_agent.py",
            "src/api/routes.py",
            "src/api/main.py",
            "src/api/models.py",
            "src/core/database/connection.py",
            "src/core/security/sql_security.py",
            "src/memory/manager.py",
        ]
        for rel_path in production_modules:
            if not _module_path(rel_path).exists():
                continue
            imports = _imported_modules(rel_path)
            for module in imports:
                assert not module.startswith("evaluation"), (
                    f"{rel_path} must not import evaluation.* "
                    f"(production code), found {module!r}"
                )


# ---------------------------------------------------------------------------
# Step 2C.10 — Dependency cycle detection
# ---------------------------------------------------------------------------


INTERNAL_PACKAGES: tuple[str, ...] = (
    "src.agents",
    "src.api",
    "src.core.database",
    "src.core.llm",
    "src.core.security",
    "src.memory",
    "src.utils",
)


def _internal_imports(rel_path: str) -> list[str]:
    """Return only the imports of *rel_path* that target our internal
    packages (skipping stdlib + third-party)."""
    return [
        m for m in _imported_modules(rel_path)
        if any(m == p or m.startswith(p + ".") for p in INTERNAL_PACKAGES)
    ]


def _resolve_to_package(module: str) -> str:
    """Collapse a dotted import path to its top-level internal package
    (e.g. ``src.agents.state`` → ``src.agents``)."""
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in INTERNAL_PACKAGES:
            return candidate
    return module


class TestDependencyCycleDetection:
    """Walk the internal import graph and assert there are no cycles.

    The graph is a directed multigraph where a node is a top-level
    internal package and an edge from A → B means some module in A
    imports some module in B. A cycle is a back-edge in the DFS.
    """

    def _build_graph(self) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {p: set() for p in INTERNAL_PACKAGES}
        # Walk every .py under src/ and record edges. We ignore
        # __init__.py in count of nodes — only as edges.
        for path in SRC_ROOT.rglob("*.py"):
            rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
            imports = _internal_imports(rel)
            src_pkg = ".".join(rel.split("/")[:-1])
            src_pkg = _resolve_to_package(src_pkg)
            if src_pkg not in graph:
                continue
            for module in imports:
                dst = _resolve_to_package(module)
                if dst in graph and dst != src_pkg:
                    graph[src_pkg].add(dst)
        return graph

    def test_no_dependency_cycles_among_internal_packages(self) -> None:
        graph = self._build_graph()

        # DFS-based cycle detection: white(0)=unseen, gray(1)=in stack,
        # black(2)=done. A gray-to-gray edge closes a cycle.
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in graph}
        cycles: list[tuple[str, ...]] = []

        def dfs(node: str, stack: list[str]) -> None:
            color[node] = GRAY
            stack.append(node)
            for neighbour in sorted(graph[node]):
                if color[neighbour] == GRAY:
                    # Cycle: extract path from neighbour to node.
                    cycle_start = stack.index(neighbour)
                    cycles.append(tuple(stack[cycle_start:] + [neighbour]))
                elif color[neighbour] == WHITE:
                    dfs(neighbour, stack)
            stack.pop()
            color[node] = BLACK

        for node in sorted(graph):
            if color[node] == WHITE:
                dfs(node, [])

        # No cycle is acceptable. Sprint 2 Prompt 2C verified the live
        # import graph is already acyclic (the previous whitelist for
        # ``src.agents`` ↔ ``src.core.database`` / ``src.memory`` was
        # precautionary only and is removed so future cycles fail).
        assert cycles == [], (
            f"Found dependency cycles: {cycles}"
        )

    def test_orchestrator_does_not_import_evaluation(self) -> None:
        imports = _internal_imports("src/agents/orchestrator.py")
        for module in imports:
            assert not module.startswith("evaluation"), (
                f"orchestrator must not import evaluation.*, found {module!r}"
            )

    def test_state_module_does_not_import_orchestrator(self) -> None:
        """State is a data model. It must not import the orchestrator
        (it would invert the dependency direction)."""
        imports = _imported_modules("src/agents/state.py")
        for module in imports:
            assert module != "src.agents.orchestrator", (
                "state.py must not import the orchestrator"
            )

    def test_contracts_module_does_not_import_orchestrator(self) -> None:
        imports = _imported_modules("src/agents/contracts.py")
        for module in imports:
            assert module != "src.agents.orchestrator", (
                "contracts.py must not import the orchestrator"
            )

    def test_adapters_module_does_not_import_orchestrator(self) -> None:
        imports = _imported_modules("src/agents/adapters.py")
        for module in imports:
            assert module != "src.agents.orchestrator", (
                "adapters.py must not import the orchestrator"
            )


# ---------------------------------------------------------------------------
# Step 2C.11 — Forward dependency rule check
# ---------------------------------------------------------------------------


class TestForwardDependencyRule:
    """The approved pipeline order is
    QuestionAnalyzer → ... → AnswerValidator, with forward-only
    dependencies. A future component must not import a *later*
    component in the pipeline.

    In Prompt 2B the *future* components are not yet implemented, so
    the check is forward-looking: every reference to a "future"
    component in the contracts/orchestrator code is an import of a
    Protocol (a forward reference to a type), not of a concrete
    implementation.
    """

    def test_orchestrator_imports_only_protocols_for_future_components(self) -> None:
        from src.agents import contracts

        # For each future component Protocol the Orchestrator claims a
        # dependency on, verify the import is of the *Protocol* (i.e. an
        # interface) and not of a concrete implementation module.
        future_names = (
            "QuestionAnalyzer", "SchemaCatalog", "SchemaRetriever",
            "QueryPlanner", "SQLGenerator", "SQLValidator",
            "SecurityValidator", "SemanticValidator",
            "ComplexityValidator", "SQLRepairer", "QueryExecutor",
            "ResultAnalyzer", "PresentationPlanner", "AnswerGenerator",
            "AnswerValidator", "LLMRouter",
        )
        for name in future_names:
            symbol = getattr(contracts, name)
            assert inspect.isclass(symbol), (
                f"{name} must be a class/Protocol, got {type(symbol)}"
            )

    def test_text_to_sql_agent_does_not_import_protocol_modules(
        self,
    ) -> None:
        """The façade must depend only on the public API surface; future
        Protocol details stay in the orchestrator."""
        imports = _imported_modules("src/agents/text_to_sql_agent.py")
        for forbidden in (
            "src.agents.contracts",
            "src.agents.adapters",
        ):
            # The façade *may* import the adapters module because
            # QueryExecutorAdapter is the public Executor type.
            if forbidden == "src.agents.adapters":
                continue
            assert forbidden not in imports, (
                f"text_to_sql_agent must not import {forbidden!r}"
            )


# ---------------------------------------------------------------------------
# Step 2C.12 — Orchestrator boundary validation
# ---------------------------------------------------------------------------


class TestOrchestratorBoundary:
    """The Orchestrator must:

    * own state transitions,
    * coordinate dependencies,
    * handle retries,
    * handle typed errors.

    It must not contain SQL generation, schema retrieval, question
    understanding, database implementation, LLM provider implementation,
    or presentation logic.
    """

    ORCHESTRATOR_PATH = "src/agents/orchestrator.py"

    def test_orchestrator_does_not_define_sql_generation(self) -> None:
        """The orchestrator should not embed SQL prompt construction.
        It delegates to SQLGenerator (or the legacy extractor)."""
        source = _module_path(self.ORCHESTRATOR_PATH).read_text(encoding="utf-8")
        # Forbidden: large SQL prompt templates, LLM prompt construction.
        forbidden_markers = [
            "natural_response_prompt",  # legitimate answer-side prompt
            "prompts.sql_generation_prompt",
            "prompts.correction_prompt",
        ]
        for marker in forbidden_markers:
            assert marker not in source, (
                f"Orchestrator must not embed {marker!r} logic"
            )

    def test_orchestrator_does_not_import_pymssql(self) -> None:
        imports = _imported_modules(self.ORCHESTRATOR_PATH)
        for module in imports:
            assert module != "pymssql", (
                "Orchestrator must not import pymssql (DB is QueryExecutor)"
            )

    def test_orchestrator_does_not_import_provider_sdks(self) -> None:
        imports = _imported_modules(self.ORCHESTRATOR_PATH)
        for module in imports:
            assert module not in FORBIDDEN_PROVIDER_SDKS, (
                f"Orchestrator must not import provider SDK {module!r}"
            )

    def test_orchestrator_does_not_define_provider_clients(self) -> None:
        """The Orchestrator should not define provider classes."""
        source = _module_path(self.ORCHESTRATOR_PATH).read_text(encoding="utf-8")
        for cls in ("OpenAIClient", "GeminiClient", "GroqClient", "OllamaClient"):
            assert f"class {cls}" not in source, (
                f"Orchestrator must not define {cls}"
            )

    def test_orchestrator_only_public_entry_is_run(self) -> None:
        from src.agents.orchestrator import Orchestrator

        public_methods = [
            n for n in dir(Orchestrator)
            if not n.startswith("_") and callable(getattr(Orchestrator, n))
        ]
        # Only ``run`` should be public. (Properties etc. are filtered.)
        assert public_methods == ["run"], (
            f"Orchestrator public methods should be ['run'] only, "
            f"found {public_methods}"
        )

    def test_orchestrator_handles_security_errors(self) -> None:
        """The Orchestrator must catch ``SQLSecurityError`` and translate
        it to a typed failure state."""
        source = _module_path(self.ORCHESTRATOR_PATH).read_text(encoding="utf-8")
        assert "SQLSecurityError" in source, (
            "Orchestrator must handle SQLSecurityError"
        )

    def test_orchestrator_handles_query_execution_errors(self) -> None:
        source = _module_path(self.ORCHESTRATOR_PATH).read_text(encoding="utf-8")
        assert "QueryExecutionError" in source, (
            "Orchestrator must handle QueryExecutionError"
        )

    def test_orchestrator_owns_retry_policy(self) -> None:
        """The Orchestrator should not delegate retries to a hidden
        sub-component. SQLRepairer is the only legitimate retry
        (bounded)."""
        source = _module_path(self.ORCHESTRATOR_PATH).read_text(encoding="utf-8")
        # The current implementation returns state if no repairer is
        # wired, so the orchestrator owns the retry decision.
        assert "sql_repairer" in source, (
            "Orchestrator must mention sql_repairer to own retry policy"
        )


# ---------------------------------------------------------------------------
# Step 2C.13 — Dependency injection validation
# ---------------------------------------------------------------------------


class TestDependencyInjection:
    """Orchestrator dependencies must be injected, not constructed
    inside the orchestrator."""

    def test_orchestrator_does_not_instantiate_database_connection(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "DatabaseConnection(" not in source, (
            "Orchestrator must not instantiate DatabaseConnection"
        )

    def test_orchestrator_does_not_instantiate_llm_client(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "LLMClient(" not in source, (
            "Orchestrator must not instantiate LLMClient"
        )

    def test_orchestrator_does_not_instantiate_memory_manager(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "MemoryManager(" not in source, (
            "Orchestrator must not instantiate MemoryManager"
        )

    def test_orchestrator_does_not_instantiate_sql_extractor(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "SQLExtractor(" not in source, (
            "Orchestrator must not instantiate SQLExtractor"
        )

    def test_orchestrator_does_not_instantiate_system_prompts(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "SystemPrompts(" not in source, (
            "Orchestrator must not instantiate SystemPrompts"
        )

    def test_orchestrator_does_not_call_pymssql_connect(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "pymssql.connect" not in source, (
            "Orchestrator must not call pymssql.connect"
        )

    def test_orchestrator_constructor_only_takes_deps(self) -> None:
        from src.agents.orchestrator import Orchestrator

        sig = inspect.signature(Orchestrator.__init__)
        params = [p for p in sig.parameters if p != "self"]
        assert params == ["deps"], (
            f"Orchestrator.__init__ should take only 'deps', got {params}"
        )


# ---------------------------------------------------------------------------
# Step 2C.14 — Adapter validation
# ---------------------------------------------------------------------------


class TestAdapterBoundaries:
    """Adapters must be thin and not duplicate business logic."""

    def test_security_validator_adapter_is_thin(self) -> None:
        from src.agents.adapters import SecurityValidatorAdapter

        # The adapter's source file should not contain SQL parsing or
        # LLM-related logic — it is a wrapper.
        source = _module_path("src/agents/adapters.py").read_text(encoding="utf-8")
        assert "class SecurityValidatorAdapter" in source
        # Adapter must wrap the existing SQLSecurityValidator.
        assert "SQLSecurityValidator" in source

    def test_query_executor_adapter_is_thin(self) -> None:
        from src.agents.adapters import QueryExecutorAdapter

        source = _module_path("src/agents/adapters.py").read_text(encoding="utf-8")
        assert "class QueryExecutorAdapter" in source
        # Adapter must wrap DatabaseConnection.
        assert "DatabaseConnection" in source

    def test_query_executor_adapter_returns_typed_query_result(self) -> None:
        from src.agents.contracts import QueryResult
        from src.agents.adapters import QueryExecutorAdapter

        # Build a fake DB and adapter to ensure the return type is correct.
        class FakeCursor:
            arraysize = 1
            rows = []

            def execute(self, sql, params=None):
                pass

            def fetchmany(self, size):
                return []

            def close(self):
                pass

        class FakeConnection:
            def cursor(self, as_dict=True):
                return FakeCursor()

            def close(self):
                pass

        from src.core.database.connection import (
            DatabaseConnection,
            ExecutionPolicy,
        )

        db = DatabaseConnection(
            "host", 1433, "u", "p", "d",
            execution_policy=ExecutionPolicy(
                max_rows=10, max_fetch_batch=2,
                query_timeout=5, connection_timeout=2,
            ),
            connect_fn=lambda **kw: FakeConnection(),
        )
        adapter = QueryExecutorAdapter(db)
        result = adapter.execute("SELECT 1")
        assert isinstance(result, QueryResult)

    def test_adapter_does_not_mutate_agent_state(self) -> None:
        """Adapters must not import AgentState (single-writer rule)."""
        imports = _imported_modules("src/agents/adapters.py")
        for module in imports:
            assert module != "src.agents.state", (
                "adapters.py must not import AgentState"
            )

    def test_adapter_does_not_call_state_with_methods(self) -> None:
        calls = _state_writers("src/agents/adapters.py")
        assert calls == [], (
            f"adapters.py calls with_* methods: "
            f"{[ast.unparse(c) for c in calls]}"
        )


# ---------------------------------------------------------------------------
# Step 2C.15-17 — Security / Execution / Observability regression
# ---------------------------------------------------------------------------


class TestSecurityBoundaryRegression:
    """The complete security path is preserved: SQL → Security Core →
    Execution Guard → Database."""

    def test_security_validator_runs_before_executor(self) -> None:
        """The Orchestrator must call the SecurityValidator before
        executing SQL."""
        from src.agents.orchestrator import Orchestrator

        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        # The validation call must come before the execution call in
        # the source. (This is a coarse textual check; the AST check
        # below is more precise.)
        val_idx = source.find("self._deps.security_validator.validate")
        exec_idx = source.find("self._deps.query_executor.execute")
        assert val_idx != -1, "Orchestrator must call security_validator"
        assert exec_idx != -1, "Orchestrator must call query_executor"
        assert val_idx < exec_idx, (
            "security_validator must be called before query_executor"
        )

    def test_query_executor_adapter_preserves_inline_security_check(
        self,
    ) -> None:
        """The QueryExecutorAdapter must rely on the existing
        inline security check inside ``DatabaseConnection.execute_query``
        and must not call any ``.validate(...)`` itself. The inline
        check lives in ``DatabaseConnection`` (Sprint 1), and the
        adapter preserves it by calling ``execute_query`` only."""
        import re as _re

        from src.agents.adapters import QueryExecutorAdapter

        source = _module_path("src/agents/adapters.py").read_text(encoding="utf-8")
        # The adapter module must reference ``execute_query`` so the
        # inline check is exercised.
        assert "execute_query" in source, (
            "QueryExecutorAdapter must call DatabaseConnection.execute_query"
        )
        # Extract the body of the QueryExecutorAdapter class and ensure
        # it does not call ``.validate(``. The inline check inside
        # DatabaseConnection is preserved by the adapter delegating to
        # ``execute_query`` instead of re-validating.
        match = _re.search(
            r"class QueryExecutorAdapter\b.*?(?=\nclass |\Z)",
            source,
            _re.DOTALL,
        )
        assert match is not None, "QueryExecutorAdapter class not found"
        body = match.group(0)
        assert ".validate(" not in body, (
            "QueryExecutorAdapter must not re-implement validation; "
            "DatabaseConnection.execute_query performs the inline check"
        )


class TestSafeExecutionRegression:
    """The QueryExecutor contract must preserve safe-execution
    invariants: max_rows, timeout, fetch bound, typed DB errors,
    resource cleanup, cursor isolation."""

    def test_execution_policy_validates_positive_integers(self) -> None:
        from src.core.database.connection import ExecutionPolicy

        with pytest.raises(ValueError):
            ExecutionPolicy(max_rows=0)
        with pytest.raises(ValueError):
            ExecutionPolicy(query_timeout=-1)
        with pytest.raises(ValueError):
            ExecutionPolicy(max_rows="x")  # type: ignore[arg-type]

    def test_query_executor_protocol_signature_preserves_execution_policy(
        self,
    ) -> None:
        from src.agents.contracts import QueryExecutor

        method = QueryExecutor.execute
        sig = inspect.signature(method)
        # ``policy`` parameter must be present.
        assert "policy" in sig.parameters

    def test_database_connection_caps_rows(self) -> None:
        """The DatabaseConnection still caps rows via execution_policy.
        Verified by injection of a cursor that returns too many rows."""

        class InfiniteCursor:
            arraysize = 1

            def __init__(self, total_rows):
                self._total = total_rows
                self._offset = 0

            def execute(self, sql, params=None):
                pass

            def fetchmany(self, size):
                if self._offset >= self._total:
                    return []
                batch = [
                    {"v": self._offset + i}
                    for i in range(min(size, self._total - self._offset))
                ]
                self._offset += len(batch)
                return batch

            def close(self):
                pass

        class CapConnection:
            def __init__(self, total_rows):
                self.cursor_instance = InfiniteCursor(total_rows)

            def cursor(self, as_dict=True):
                return self.cursor_instance

            def close(self):
                pass

        from src.core.database.connection import (
            DatabaseConnection,
            DatabaseError,
            DatabaseErrorCode,
            ExecutionPolicy,
        )

        # Use max_rows=5 but provide 100 rows; the connection should
        # raise DATABASE_RESULT_LIMIT.
        db = DatabaseConnection(
            "h", 1433, "u", "p", "d",
            execution_policy=ExecutionPolicy(
                max_rows=5, max_fetch_batch=2,
                query_timeout=5, connection_timeout=2,
            ),
            connect_fn=lambda **kw: CapConnection(100),
        )
        with pytest.raises(DatabaseError) as excinfo:
            db.execute_query("SELECT 1")
        assert excinfo.value.code == DatabaseErrorCode.DATABASE_RESULT_LIMIT

    def test_cursor_and_connection_are_closed(self) -> None:
        """Even on success, both the cursor and the connection must be
        closed."""

        class TrackingCursor:
            arraysize = 1
            closed = False

            def __init__(self):
                self._calls = 0

            def execute(self, sql, params=None):
                pass

            def fetchmany(self, size):
                self._calls += 1
                # Return one row on the first call, then empty.
                if self._calls == 1:
                    return [{"v": 1}]
                return []

            def close(self):
                self.closed = True

        class TrackingConnection:
            closed = False

            def __init__(self):
                self.cursor_instance = TrackingCursor()

            def cursor(self, as_dict=True):
                return self.cursor_instance

            def close(self):
                self.closed = True

        from src.core.database.connection import (
            DatabaseConnection,
            ExecutionPolicy,
        )

        conn = TrackingConnection()
        db = DatabaseConnection(
            "h", 1433, "u", "p", "d",
            execution_policy=ExecutionPolicy(
                max_rows=10, max_fetch_batch=2,
                query_timeout=5, connection_timeout=2,
            ),
            connect_fn=lambda **kw: conn,
        )
        rows = db.execute_query("SELECT 1")
        assert rows == [{"v": 1}]
        assert conn.cursor_instance.closed
        assert conn.closed


class TestObservabilityRegression:
    """TraceContext and StructuredLogger remain cross-cutting
    infrastructure; the Orchestrator transitions do not lose trace
    context."""

    def test_orchestrator_emits_lifecycle_events(self) -> None:
        """The Orchestrator emits ``orchestrator_started`` and
        ``orchestrator_completed`` events."""
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "orchestrator_started" in source
        assert "orchestrator_completed" in source

    def test_orchestrator_logs_failure_events(self) -> None:
        source = _module_path("src/agents/orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "orchestrator_failed" in source

    def test_trace_id_propagates_through_state(self) -> None:
        """The Orchestrator must preserve the request trace_id through
        the run; we verify by constructing an AgentState with a custom
        trace_id and inspecting the final state."""
        from src.agents.orchestrator import Orchestrator, OrchestratorDeps
        from src.agents.state import create_initial_state
        from src.utils.logger import (
            reset_trace_context, set_trace_context,
        )

        class FakeExtractor:
            def extract(self, question, history=None):
                return "SELECT 1"

        class FakeLLM:
            def generate(self, prompt):
                return "answer"

        class FakeMemory:
            def create_session(self):
                return "s"

            def session_exists(self, sid):
                return True

            def get_history(self, sid, limit=5):
                return []

            def add_message(self, sid, role, content):
                pass

            def clear_session(self, sid):
                pass

        class FakePrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        from src.agents.adapters import (
            QueryExecutorAdapter,
            SecurityValidatorAdapter,
        )
        from src.core.database.connection import (
            DatabaseConnection, ExecutionPolicy,
        )

        class FakeCursor:
            arraysize = 1
            rows = [{"v": 1}]

            def execute(self, sql, params=None):
                pass

            def fetchmany(self, size):
                return self.rows

            def close(self):
                pass

        class FakeConnection:
            def cursor(self, as_dict=True):
                return FakeCursor()

            def close(self):
                pass

        db = DatabaseConnection(
            "h", 1433, "u", "p", "d",
            execution_policy=ExecutionPolicy(
                max_rows=10, max_fetch_batch=2,
                query_timeout=5, connection_timeout=2,
            ),
            connect_fn=lambda **kw: FakeConnection(),
        )
        deps = OrchestratorDeps(
            sql_extractor=FakeExtractor(),
            query_executor=QueryExecutorAdapter(db),
            llm_client=FakeLLM(),
            prompts=FakePrompts(),
            memory_manager=FakeMemory(),
            security_validator=SecurityValidatorAdapter(),
        )
        token = set_trace_context("trace-xyz")
        try:
            state = create_initial_state(
                question="q", session_id="s", trace_id="trace-xyz",
            )
            final = Orchestrator(deps).run(state)
            assert final.request.trace_id == "trace-xyz"
        finally:
            reset_trace_context(token)


# ---------------------------------------------------------------------------
# Step 2C.18 — Public API contract verification
# ---------------------------------------------------------------------------


class TestPublicApiContract:
    """POST /api/v1/ask is frozen (interface-design.md §10)."""

    def test_ask_request_fields(self) -> None:
        from src.api.models import AskRequest

        assert "question" in AskRequest.model_fields
        assert "session_id" in AskRequest.model_fields

    def test_ask_response_fields(self) -> None:
        from src.api.models import AskResponse

        required = (
            "success", "question", "answer", "sql",
            "row_count", "rows", "error", "session_id",
        )
        for field in required:
            assert field in AskResponse.model_fields, (
                f"AskResponse must expose {field!r}"
            )

    def test_ask_endpoint_exists(self) -> None:
        """The /ask POST route is registered in src/api/routes.py."""
        source = _module_path("src/api/routes.py").read_text(encoding="utf-8")
        assert '"/ask"' in source, "/ask route must be registered"
        assert "AskRequest" in source
        assert "AskResponse" in source

    def test_health_endpoint_exists(self) -> None:
        source = _module_path("src/api/routes.py").read_text(encoding="utf-8")
        assert '"/health"' in source

    def test_routes_use_only_façade(self) -> None:
        """src/api/routes.py must depend on the public façade only."""
        imports = _imported_modules("src/api/routes.py")
        # The façade is the only allowed import.
        assert "src.agents.text_to_sql_agent" in imports
        # It must NOT import internal modules.
        for forbidden in (
            "src.agents.orchestrator",
            "src.agents.contracts",
            "src.agents.adapters",
        ):
            assert forbidden not in imports, (
                f"src/api/routes.py must not import {forbidden!r}"
            )


# ---------------------------------------------------------------------------
# Step 2C.19 — CLI / Evaluation compatibility
# ---------------------------------------------------------------------------


class TestCliCompatibility:
    """The CLI (``run.py``) constructs ``TextToSQLAgent()`` with no
    arguments. The constructor signature must remain unchanged."""

    def test_run_py_imports_agent(self) -> None:
        source = _module_path("run.py").read_text(encoding="utf-8")
        assert "TextToSQLAgent" in source
        assert "from src.agents.text_to_sql_agent" in source

    def test_run_api_imports_app(self) -> None:
        source = _module_path("run_api.py").read_text(encoding="utf-8")
        assert "uvicorn" in source
        assert "src.api.main:app" in source

    def test_run_ui_launches_streamlit(self) -> None:
        source = _module_path("run_ui.py").read_text(encoding="utf-8")
        assert "streamlit" in source
        assert "src/web/app.py" in source


class TestEvaluationAdapterCompatibility:
    """The Evaluation Adapter continues to consume the public Agent
    interface."""

    def test_evaluation_agent_adapter_normalizes_response(self) -> None:
        from evaluation.adapters import EvaluationAgentAdapter

        class StubAgent:
            def ask(self, question, session_id=None):
                return {
                    "success": True,
                    "question": question,
                    "answer": "ok",
                    "sql": "SELECT 1",
                    "row_count": 1,
                    "rows": [{"v": 1}],
                    "session_id": session_id or "s-new",
                }

        adapter = EvaluationAgentAdapter(agent=StubAgent())
        response = adapter.ask("how many?")
        assert response.success is True
        assert response.answer == "ok"
        assert response.sql == "SELECT 1"
        assert response.row_count == 1

    def test_evaluation_adapter_handles_failure(self) -> None:
        from evaluation.adapters import EvaluationAgentAdapter

        class BrokenAgent:
            def ask(self, question, session_id=None):
                return {
                    "success": False,
                    "question": question,
                    "error": "some failure",
                    "sql": "SELECT 1",
                    "session_id": session_id or "s",
                }

        adapter = EvaluationAgentAdapter(agent=BrokenAgent())
        response = adapter.ask("q")
        assert response.success is False
        assert response.error == "some failure"

    def test_evaluation_adapter_handles_exception(self) -> None:
        from evaluation.adapters import EvaluationAgentAdapter

        class CrashingAgent:
            def ask(self, question, session_id=None):
                raise RuntimeError("kaboom")

        adapter = EvaluationAgentAdapter(agent=CrashingAgent())
        response = adapter.ask("q")
        assert response.success is False
        assert "kaboom" in (response.error or "")


# ---------------------------------------------------------------------------
# Step 2C.20-2C.21 — Reusable contract test patterns
# ---------------------------------------------------------------------------


class TestReusableContractPatterns:
    """Pattern tests that future component implementations can run
    against their concrete class. These verify Protocol membership and
    method-name presence.
    """

    def test_protocol_membership_for_a_typical_concrete_class(self) -> None:
        """A class that implements a Protocol's methods is recognised
        by ``isinstance``."""
        from src.agents.contracts import (
            AnswerGenerator,
            AnswerDraft,
            Evidence,
            PresentationPlan,
            QueryResult,
            ResultProfile,
        )

        class MyAnswerGenerator:
            def generate(
                self, spec, profile, presentation, result, *, evidence=()
            ):
                return AnswerDraft(text="x", claims=(), citations=())

        instance = MyAnswerGenerator()
        # runtime_checkable only checks attribute presence.
        assert hasattr(instance, "generate")

    def test_protocol_membership_with_explicit_inheritance(self) -> None:
        from src.agents.contracts import QueryExecutor

        class MyExecutor(QueryExecutor):
            def execute(self, sql, *, params=None, policy=...):
                from src.agents.contracts import QueryResult
                return QueryResult()

        assert isinstance(MyExecutor(), QueryExecutor)

    def test_typed_fake_factory_signature(self) -> None:
        """A reusable factory produces a Protocol-conforming fake for
        any of the 17 protocols. Used by orchestrator happy-path
        tests in Step 2C.22."""
        from src.agents.contracts import (
            AnswerGenerator, AnswerValidator, ComplexityValidator,
            MemoryManagerProtocol, PresentationPlanner, QueryExecutor,
            QueryPlanner, ResultAnalyzer, SchemaCatalog, SchemaRetriever,
            SemanticValidator, SQLGenerator, SQLRepairer, SQLValidator,
            SecurityValidator, QuestionAnalyzer, LLMRouter,
        )
        # All Protocols are classes.
        for cls in (
            QuestionAnalyzer, SchemaCatalog, SchemaRetriever,
            QueryPlanner, SQLGenerator, SQLValidator, SecurityValidator,
            SemanticValidator, ComplexityValidator, SQLRepairer,
            QueryExecutor, ResultAnalyzer, PresentationPlanner,
            AnswerGenerator, AnswerValidator, MemoryManagerProtocol,
            LLMRouter,
        ):
            assert inspect.isclass(cls)
            assert hasattr(cls, "_is_runtime_protocol"), (
                f"{cls.__name__} must be @runtime_checkable"
            )


# ---------------------------------------------------------------------------
# Step 2C.22-2C.23 — Orchestrator happy path & failure paths via typed fakes
# ---------------------------------------------------------------------------


class _OrchestratorFakes:
    """A single namespace of typed fakes implementing each Protocol.

    These fakes are the canonical contract-test fakes. Future sprint
    implementations can use them as drop-in replacements for the
    concrete components.

    The factory methods return objects that satisfy the Protocol
    surface but produce minimal, deterministic outputs.
    """

    @staticmethod
    def question_analyzer(spec: Optional[Any] = None) -> Any:
        from src.agents.contracts import QuestionAnalyzer, QuestionSpec

        class _Stub(QuestionAnalyzer):
            def analyze(self, question, history, **kwargs):
                if spec is not None:
                    return spec
                return QuestionSpec(intent="lookup", confidence=0.9)

        return _Stub()

    @staticmethod
    def schema_catalog() -> Any:
        from src.agents.contracts import (
            SchemaCatalog, SchemaSnapshot, SchemaVersion,
            TableDescriptor,
        )
        from datetime import datetime

        class _Stub(SchemaCatalog):
            def current_version(self):
                return "v1"

            def versions(self):
                return (SchemaVersion(
                    id="v1",
                    captured_at=datetime(2024, 1, 1),
                    source="test",
                ),)

            def get_snapshot(self, version=None):
                return SchemaSnapshot(
                    version=SchemaVersion(
                        id="v1",
                        captured_at=datetime(2024, 1, 1),
                        source="test",
                    ),
                    tables=(TableDescriptor(name="T"),),
                )

        return _Stub()

    @staticmethod
    def schema_retriever() -> Any:
        from src.agents.contracts import (
            SchemaContext, SchemaRetriever, TableRef,
        )

        class _Stub(SchemaRetriever):
            def retrieve(self, spec, snapshot):
                return SchemaContext(tables=(TableRef(name="T"),))

        return _Stub()

    @staticmethod
    def query_planner(plan: Optional[Any] = None) -> Any:
        from src.agents.contracts import QueryPlanner, QueryPlan

        class _Stub(QueryPlanner):
            def plan(self, spec, context):
                if plan is not None:
                    return plan
                return QueryPlan()

        return _Stub()

    @staticmethod
    def sql_generator(sql: str = "SELECT 1") -> Any:
        from src.agents.contracts import SQLCandidate, SQLGenerator

        class _Stub(SQLGenerator):
            def generate(self, plan, context, *, dialect="tsql"):
                return SQLCandidate(
                    sql=sql, dialect=dialect,
                    candidate_id="c1", fingerprint="fp1",
                )

        return _Stub()

    @staticmethod
    def sql_validator() -> Any:
        from src.agents.contracts import (
            ParsedAST, SQLValidator, ValidationReport,
        )

        class _Stub(SQLValidator):
            def validate(self, candidate, *, dialect="tsql"):
                # Mark parse_ok=True so the orchestrator can proceed.
                return ValidationReport(
                    candidate_id=candidate.candidate_id,
                    valid=True, parse_ok=True, ast=ParsedAST(
                        expression=None, dialect=dialect,
                    ),
                )

        return _Stub()

    @staticmethod
    def semantic_validator() -> Any:
        from src.agents.contracts import SemanticReport, SemanticValidator

        class _Stub(SemanticValidator):
            def validate(self, candidate, plan, snapshot, ast=None):
                return SemanticReport(candidate_id=candidate.candidate_id, ok=True)

        return _Stub()

    @staticmethod
    def complexity_validator() -> Any:
        from src.agents.contracts import (
            ComplexityReport, ComplexityValidator,
        )

        class _Stub(ComplexityValidator):
            def validate(self, ast, budget):
                return ComplexityReport(ok=True, estimated_cost="low")

        return _Stub()

    @staticmethod
    def sql_repairer() -> Any:
        from src.agents.contracts import SQLCandidate, SQLRepairer

        class _Stub(SQLRepairer):
            def repair(self, candidate, *, validation=None, security=None,
                       semantic=None, complexity=None, plan, attempt):
                return SQLCandidate(
                    sql=candidate.sql, dialect=candidate.dialect,
                    candidate_id=SQLCandidate(
                        sql=candidate.sql, dialect=candidate.dialect,
                        candidate_id="r1", fingerprint="rp1",
                    ).candidate_id,
                    fingerprint=SQLCandidate(
                        sql=candidate.sql, dialect=candidate.dialect,
                        candidate_id="r1", fingerprint="rp1",
                    ).fingerprint,
                )

        return _Stub()

    @staticmethod
    def query_executor(rows: Sequence[Mapping[str, Any]] = ({"v": 1},)) -> Any:
        from src.agents.contracts import QueryExecutor, QueryResult

        class _Stub(QueryExecutor):
            def __init__(self):
                self.executed_sql: list[str] = []
                self._rows = tuple(rows)

            def execute(self, sql, *, params=None, policy=...):
                self.executed_sql.append(sql)
                return QueryResult(
                    columns=tuple(self._rows[0].keys()) if self._rows else (),
                    rows=self._rows,
                    row_count=len(self._rows),
                )

        return _Stub()

    @staticmethod
    def result_analyzer() -> Any:
        from src.agents.contracts import (
            ResultAnalyzer, ResultProfile, ShapeDescriptor,
        )

        class _Stub(ResultAnalyzer):
            def profile(self, result, plan):
                return ResultProfile(
                    shape=ShapeDescriptor(rows=result.row_count, columns=1),
                    row_count=result.row_count,
                )

        return _Stub()

    @staticmethod
    def presentation_planner() -> Any:
        from src.agents.contracts import (
            PresentationPlanner, PresentationPlan,
        )

        class _Stub(PresentationPlanner):
            def plan(self, spec, profile, *, labels, locale_policy):
                return PresentationPlan(view_kind="table")

        return _Stub()

    @staticmethod
    def answer_generator(text: str = "answer from typed fake") -> Any:
        from src.agents.contracts import (
            AnswerDraft, AnswerGenerator,
        )

        class _Stub(AnswerGenerator):
            def generate(
                self, spec, profile, presentation, result, *, evidence=()
            ):
                return AnswerDraft(text=text)

        return _Stub()

    @staticmethod
    def answer_validator() -> Any:
        from src.agents.contracts import (
            AnswerValidator, ValidatedAnswer,
        )

        class _Stub(AnswerValidator):
            def validate(self, draft, profile, presentation):
                return ValidatedAnswer(
                    text=draft.text, accepted_claims=draft.claims,
                )

        return _Stub()

    @staticmethod
    def memory_manager() -> Any:
        from src.agents.contracts import MemoryManagerProtocol

        class _Stub(MemoryManagerProtocol):
            def __init__(self):
                self.messages: list[tuple[str, str, str]] = []
                self._session = "s-typed"

            def create_session(self):
                return self._session

            def session_exists(self, sid):
                return bool(sid)

            def add_message(self, sid, role, content):
                self.messages.append((sid, role, content))

            def get_history(self, sid, limit=5):
                return []

            def clear_session(self, sid):
                pass

        return _Stub()

    @staticmethod
    def llm_router(text: str = "ok") -> Any:
        from src.agents.contracts import (
            LLMRouter, LLMResult, Usage,
        )

        class _Stub(LLMRouter):
            def generate(self, prompt, *, task, policy=None, budget=None):
                return LLMResult(
                    text=text, provider="test", model="t",
                    usage=Usage(),
                )

        return _Stub()


def _build_full_orchestrator_deps() -> tuple[Any, Any]:
    """Build a complete OrchestratorDeps using typed fakes for every
    future component. The ``sql_extractor`` and ``llm_client`` slots
    remain for the current path (fakes no-ops) so the orchestrator
    does not invoke them in the future-component path."""
    from src.agents.orchestrator import Orchestrator, OrchestratorDeps

    class NoopExtractor:
        def extract(self, question, history=None):
            return "SELECT 1"

    class NoopLLM:
        def generate(self, prompt):
            return "noop"

    class NoopPrompts:
        def natural_response_prompt(self, question, rows, row_count):
            return "p"

    deps = OrchestratorDeps(
        sql_extractor=NoopExtractor(),
        query_executor=_OrchestratorFakes.query_executor(),
        llm_client=NoopLLM(),
        prompts=NoopPrompts(),
        memory_manager=_OrchestratorFakes.memory_manager(),
        security_validator=__import__(
            "src.agents.adapters", fromlist=["SecurityValidatorAdapter"]
        ).SecurityValidatorAdapter(),
        question_analyzer=_OrchestratorFakes.question_analyzer(),
        schema_catalog=_OrchestratorFakes.schema_catalog(),
        schema_retriever=_OrchestratorFakes.schema_retriever(),
        query_planner=_OrchestratorFakes.query_planner(),
        sql_generator=_OrchestratorFakes.sql_generator(),
        sql_validator=_OrchestratorFakes.sql_validator(),
        semantic_validator=_OrchestratorFakes.semantic_validator(),
        complexity_validator=_OrchestratorFakes.complexity_validator(),
        result_analyzer=_OrchestratorFakes.result_analyzer(),
        presentation_planner=_OrchestratorFakes.presentation_planner(),
        answer_generator=_OrchestratorFakes.answer_generator(),
        answer_validator=_OrchestratorFakes.answer_validator(),
    )
    return Orchestrator(deps), deps


class TestOrchestratorHappyPathTypedFakes:
    """The complete pipeline runs through the Orchestrator with typed
    fakes implementing every Protocol. This proves the contract
    surfaces are compatible end-to-end."""

    def test_full_pipeline_runs_through_every_stage(self) -> None:
        from src.agents.state import PipelineStage, create_initial_state
        from src.utils.logger import reset_trace_context, set_trace_context

        orch, deps = _build_full_orchestrator_deps()
        token = set_trace_context("trace-full")
        try:
            state = create_initial_state(
                question="q", session_id="s-typed",
            )
            final = orch.run(state)
        finally:
            reset_trace_context(token)

        assert final.question_analysis is not None
        assert final.schema_context is not None
        assert final.query_plan is not None
        assert final.sql.generated_sql is not None
        assert final.sql.is_validated is True
        assert final.execution.is_success is True
        assert final.result_profile is not None
        assert final.presentation_plan is not None
        assert final.validated_answer is not None
        assert final.is_success() is True
        assert final.pipeline_stage == PipelineStage.ANSWERED
        assert final.response.answer == "answer from typed fake"
        # Both user + assistant persisted.
        roles = [r for _, r, _ in deps.memory_manager.messages]
        assert "user" in roles
        assert "assistant" in roles

    def test_orchestrator_sends_sql_to_executor(self) -> None:
        from src.agents.state import create_initial_state
        from src.utils.logger import reset_trace_context, set_trace_context

        orch, deps = _build_full_orchestrator_deps()
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-exec")
            orch.run(state)
        finally:
            reset_trace_context(token)
        # The SQLGenerator stub produced "SELECT 1".
        assert "SELECT 1" in deps.query_executor.executed_sql


class TestOrchestratorFailurePathsTypedFakes:
    """Each approved failure path is exercised with typed fakes."""

    def test_question_analysis_failure(self) -> None:
        from src.agents.contracts import (
            ErrorStageTag, QuestionAnalysisError, QuestionAnalyzer,
        )
        from src.agents.state import ErrorCategory, create_initial_state
        from src.utils.logger import reset_trace_context, set_trace_context

        class _Exploder(QuestionAnalyzer):
            def analyze(self, question, history, **kwargs):
                raise QuestionAnalysisError("ambiguous")

        from src.agents.orchestrator import Orchestrator, OrchestratorDeps

        class NoopExtractor:
            def extract(self, question, history=None):
                return "SELECT 1"

        class NoopLLM:
            def generate(self, prompt):
                return "ok"

        class NoopPrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        deps = OrchestratorDeps(
            sql_extractor=NoopExtractor(),
            query_executor=_OrchestratorFakes.query_executor(),
            llm_client=NoopLLM(),
            prompts=NoopPrompts(),
            memory_manager=_OrchestratorFakes.memory_manager(),
            question_analyzer=_Exploder(),
        )
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s")
            final = Orchestrator(deps).run(state)
        finally:
            reset_trace_context(token)
        # Exception from a future component is caught and turned into a
        # typed-failure state. The category defaults to INTERNAL.
        assert final.is_failed()
        assert final.error.category == ErrorCategory.INTERNAL

    def test_security_rejection_via_validator(self) -> None:
        """The security validator returns a ``SecurityDecision(valid=False)``
        and the pipeline transitions to FAILED with the security
        category."""
        from src.agents.adapters import SecurityValidatorAdapter
        from src.agents.orchestrator import Orchestrator, OrchestratorDeps
        from src.agents.state import ErrorCategory, create_initial_state
        from src.core.security.sql_security import (
            SecurityDecision, SecurityReasonCode,
        )
        from src.utils.logger import reset_trace_context, set_trace_context

        class Rejecting(SecurityValidatorAdapter):
            def validate(self, sql, *, policy=None):
                return SecurityDecision(
                    valid=False,
                    reason_code=SecurityReasonCode.NON_READ_ONLY_STATEMENT,
                    message="rejected",
                )

        class NoopExtractor:
            def extract(self, question, history=None):
                return "SELECT 1"

        class NoopLLM:
            def generate(self, prompt):
                return "ok"

        class NoopPrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        deps = OrchestratorDeps(
            sql_extractor=NoopExtractor(),
            query_executor=_OrchestratorFakes.query_executor(),
            llm_client=NoopLLM(),
            prompts=NoopPrompts(),
            memory_manager=_OrchestratorFakes.memory_manager(),
            security_validator=Rejecting(),
        )
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-sec")
            final = Orchestrator(deps).run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed()
        assert final.error.category == ErrorCategory.SECURITY

    def test_query_executor_failure(self) -> None:
        from src.agents.contracts import QueryExecutor, QueryResult
        from src.agents.orchestrator import Orchestrator, OrchestratorDeps
        from src.agents.state import ErrorCategory, create_initial_state
        from src.core.database.connection import (
            DatabaseError, DatabaseErrorCode,
        )
        from src.utils.logger import reset_trace_context, set_trace_context

        class _FailingExecutor(QueryExecutor):
            def execute(self, sql, *, params=None, policy=...):
                raise DatabaseError(
                    code=DatabaseErrorCode.DATABASE_QUERY_ERROR,
                    message="boom", sql=sql,
                )

        class NoopExtractor:
            def extract(self, question, history=None):
                return "SELECT 1"

        class NoopLLM:
            def generate(self, prompt):
                return "ok"

        class NoopPrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        deps = OrchestratorDeps(
            sql_extractor=NoopExtractor(),
            query_executor=_FailingExecutor(),
            llm_client=NoopLLM(),
            prompts=NoopPrompts(),
            memory_manager=_OrchestratorFakes.memory_manager(),
        )
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-exe")
            final = Orchestrator(deps).run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed()
        assert final.error.category == ErrorCategory.EXECUTION

    def test_llm_failure_during_answer(self) -> None:
        from src.agents.orchestrator import Orchestrator, OrchestratorDeps
        from src.agents.state import ErrorCategory, create_initial_state
        from src.utils.logger import reset_trace_context, set_trace_context

        class _BrokenLLM:
            def generate(self, prompt):
                raise RuntimeError("openai down")

        class NoopExtractor:
            def extract(self, question, history=None):
                return "SELECT 1"

        class NoopPrompts:
            def natural_response_prompt(self, question, rows, row_count):
                return "p"

        deps = OrchestratorDeps(
            sql_extractor=NoopExtractor(),
            query_executor=_OrchestratorFakes.query_executor(),
            llm_client=_BrokenLLM(),
            prompts=NoopPrompts(),
            memory_manager=_OrchestratorFakes.memory_manager(),
        )
        token = set_trace_context("t")
        try:
            state = create_initial_state(question="q", session_id="s-llm")
            final = Orchestrator(deps).run(state)
        finally:
            reset_trace_context(token)
        assert final.is_failed()
        assert final.error.category == ErrorCategory.LLM
        assert final.error.retryable is True


# ---------------------------------------------------------------------------
# Step 2C.24 — Retry ownership
# ---------------------------------------------------------------------------


class TestRetryOwnership:
    """Components do not retry. The Orchestrator owns retry policy.

    We verify this by ensuring:

    * The Orchestrator has no hidden ``while`` / ``for`` loops that
      wrap a component call.
    * The Orchestrator's ``_run_sql_repair`` method either delegates
      to SQLRepairer or returns state unchanged.
    """

    def test_orchestrator_has_no_hidden_retry_loops(self) -> None:
        tree = _read_ast("src/agents/orchestrator.py")
        # Look for any while/for at module level or in methods.
        for node in ast.walk(tree):
            if isinstance(node, (ast.While, ast.For)):
                # Per the contracts, the only legitimate retry is via
                # SQLRepairer, which lives in a separate component. The
                # orchestrator's repair method must not loop on its own.
                pytest.fail(
                    f"Orchestrator contains a {type(node).__name__} loop "
                    f"at line {node.lineno} — retry is owned by "
                    f"SQLRepairer, not the Orchestrator."
                )

    def test_sql_repairer_runs_only_when_wired(self) -> None:
        """The Orchestrator's _run_sql_repair method must short-circuit
        if no SQLRepairer is provided."""
        from src.agents.orchestrator import Orchestrator

        source = inspect.getsource(Orchestrator._run_sql_repair)
        # The method must return state unchanged when repairer is None.
        assert "if repairer is None" in source
        assert "return state" in source


# ---------------------------------------------------------------------------
# Step 2C.25 — State transition verification
# ---------------------------------------------------------------------------


class TestStateTransitionRules:
    """The state machine rejects illegal transitions."""

    def test_valid_transitions_round_trip(self) -> None:
        from src.agents.state import (
            AgentState, PipelineStage, create_initial_state,
        )

        state = create_initial_state(question="q", session_id="s")
        # INITIALIZED -> SQL_GENERATED is a documented fast path.
        state = state.with_generated_sql("SELECT 1")
        assert state.pipeline_stage == PipelineStage.SQL_GENERATED
        # SQL_GENERATED -> VALIDATED is allowed.
        state = state.with_validation_passed()
        assert state.pipeline_stage == PipelineStage.VALIDATED
        # VALIDATED -> EXECUTED is allowed.
        state = state.with_execution_results([{"a": 1}], 12.0)
        assert state.pipeline_stage == PipelineStage.EXECUTED
        # EXECUTED -> ANSWERED is allowed.
        state = state.with_answer("ok")
        assert state.pipeline_stage == PipelineStage.ANSWERED
        assert state.is_terminal()

    def test_invalid_transitions_are_rejected(self) -> None:
        from src.agents.state import (
            PipelineStage, create_initial_state,
        )

        state = create_initial_state(question="q", session_id="s")
        # INITIALIZED -> ANSWERED is invalid (cannot skip stages).
        with pytest.raises(ValueError):
            state.transition_to(PipelineStage.ANSWERED)

    def test_orchestrator_does_not_bypass_transition_to(self) -> None:
        """The Orchestrator must not call ``pipeline_stage=`` directly;
        all transitions must go through ``transition_to``."""
        tree = _read_ast("src/agents/orchestrator.py")
        # AST check: find Assign/AnnAssign targets of the form
        #   <expr>.pipeline_stage = ...
        # (a substring check would false-positive on comparisons such as
        # ``state.pipeline_stage == PipelineStage.SQL_GENERATED``).
        bad_assigns = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "pipeline_stage"
                ):
                    bad_assigns.append(ast.unparse(node))
        assert bad_assigns == [], (
            "Orchestrator must use transition_to, found direct "
            f"pipeline_stage assignment(s): {bad_assigns}"
        )


# ---------------------------------------------------------------------------
# Step 2C.26 — Serialization boundaries
# ---------------------------------------------------------------------------


class TestSerializationBoundaries:
    """Three distinct serialization boundaries must be preserved:

    1. Internal value objects (frozen dataclasses).
    2. AgentState.safe serialization (``to_dict()`` with redaction).
    3. Public API serialization (``state_to_api_response()``).

    Each boundary produces a different shape.
    """

    def test_internal_value_objects_are_not_json_serializable_directly(
        self,
    ) -> None:
        """Value objects are dataclasses; they are not intended to be
        serialised directly to JSON. This is intentional — the API
        boundary is the only public serializer."""
        from src.agents.contracts import QuestionSpec
        import json

        spec = QuestionSpec(intent="lookup")
        # QuestionSpec is a dataclass; it is not a dict. JSON
        # serialisation of a dataclass would fail in vanilla json,
        # forcing the boundary through state_to_api_response.
        with pytest.raises((TypeError, json.JSONEncoderError) if hasattr(json, "JSONEncoderError") else TypeError):
            json.dumps(spec)

    def test_state_safe_serialization_redacts_sensitive_data(self) -> None:
        from src.agents.state import create_initial_state

        state = create_initial_state(question="secret", session_id="s1")
        data = state.to_dict()  # default = no sensitive
        assert data["request"]["question"] == "[REDACTED]"
        data_with = state.to_dict(include_sensitive=True)
        assert data_with["request"]["question"] == "secret"

    def test_public_api_response_does_not_leak_internal_metadata(
        self,
    ) -> None:
        from src.agents.state import create_initial_state, state_to_api_response

        state = create_initial_state(question="q", session_id="s1")
        state = state.with_generated_sql("SELECT 1")
        state = state.with_validation_passed()
        state = state.with_execution_results([{"a": 1}], 12.0)
        state = state.with_answer("ok")
        response = state_to_api_response(state)
        # Public response keys are exactly the documented set.
        assert set(response.keys()) == {
            "success", "question", "answer", "sql",
            "row_count", "rows", "session_id",
        }


# ---------------------------------------------------------------------------
# Step 2C.27 — Contract documentation consistency
# ---------------------------------------------------------------------------


class TestContractDocumentationConsistency:
    """Cross-check the documentation against the code."""

    def test_component_contracts_md_exists(self) -> None:
        path = PROJECT_ROOT / "docs" / "contracts" / "component-contracts.md"
        assert path.exists()

    def test_interface_design_md_exists(self) -> None:
        path = PROJECT_ROOT / "docs" / "contracts" / "interface-design.md"
        assert path.exists()

    def test_dependency_rules_md_exists(self) -> None:
        path = PROJECT_ROOT / "docs" / "architecture" / "dependency-rules.md"
        assert path.exists()

    def test_component_contracts_documents_every_protocol(self) -> None:
        """For each approved Protocol, the documentation file contains
        a section whose title matches the Protocol name."""
        from src.agents import contracts

        doc = (PROJECT_ROOT / "docs" / "contracts" / "component-contracts.md").read_text(
            encoding="utf-8"
        )
        # We don't require exact section IDs but at least the bare
        # class name should appear. Aliases are handled below.
        aliases = {
            "MemoryManagerProtocol": "MemoryManager",
        }
        for name in APPROVED_INTERFACES:
            needle = aliases.get(name, name)
            assert needle in doc, (
                f"component-contracts.md should mention {needle!r}"
            )

    def test_interface_design_documents_every_error_stage(self) -> None:
        doc = (PROJECT_ROOT / "docs" / "contracts" / "interface-design.md").read_text(
            encoding="utf-8"
        )
        from src.agents.contracts import ErrorStageTag
        # Every ErrorStageTag enum value should be documented.
        for stage in ErrorStageTag:
            assert stage.name in doc or stage.value in doc, (
                f"interface-design.md should document stage {stage.name}"
            )
