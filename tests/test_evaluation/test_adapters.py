import json

import pytest

from evaluation.adapters import DatasetAdapter, EvaluationAgentAdapter


def test_repository_datasets_are_supported():
    assert len(DatasetAdapter("docs/evaluation/initial-dataset.json").load()) == 25
    assert len(DatasetAdapter("evaluation/baseline_questions.json").load()) == 102


def test_parses_list_dataset(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps([{
        "id": "c1", "question": "q", "category": "simple", "difficulty": "easy",
        "expected_intent": "count", "expected_tables": ["T"], "expected_columns": ["c"],
        "expected_behavior": "works"
    }]), encoding="utf-8")
    case = DatasetAdapter(path).load()[0]
    assert case.id == "c1"
    assert case.expected_tables == ["T"]


def test_parses_cases_object_and_preserves_missing_optional_fields(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"meta": {}, "cases": [{
        "id": "c1", "question": "q", "category": "simple", "difficulty": "hard",
        "notes": "n", "expected_behavior": "review manually"
    }]}), encoding="utf-8")
    case = DatasetAdapter(path).load()[0]
    assert case.expected_intent is None
    assert case.expected_tables is None
    assert case.notes == "n"


def test_rejects_duplicate_ids(tmp_path):
    record = {"id": "c1", "question": "q", "category": "x", "difficulty": "easy"}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps([record, record]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        DatasetAdapter(path).load()


def test_agent_exception_becomes_failure_response():
    class FailingAgent:
        def ask(self, question, session_id=None):
            raise RuntimeError("boom")

    response = EvaluationAgentAdapter(agent=FailingAgent()).ask("q")
    assert response.success is False
    assert "RuntimeError" in response.error
    assert response.latency_ms is not None


def test_normalizes_success_response():
    class Agent:
        def ask(self, question, session_id=None):
            return {"success": True, "question": question, "sql": "SELECT 1", "rows": [{}]}

    response = EvaluationAgentAdapter(agent=Agent()).ask("q")
    assert response.row_count == 1
    assert response.sql == "SELECT 1"


def test_malformed_agent_response_is_typed_invalid_response():
    class Agent:
        def ask(self, question, session_id=None):
            return "not a mapping"

    response = EvaluationAgentAdapter(agent=Agent()).ask("q")
    assert response.success is False
    assert response.error.startswith("Invalid response:")
