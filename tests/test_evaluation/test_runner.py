import json
from datetime import date
from decimal import Decimal

from evaluation.adapters import EvaluationAgentAdapter
from evaluation.models import CanonicalCase
from evaluation.runner import EvaluationRunner, compare_runs, load_run, run_markdown, save_run


def cases():
    return [
        CanonicalCase("c1", "one", "simple", "easy"),
        CanonicalCase("c2", "two", "simple", "medium"),
    ]


class FakeAgent:
    def ask(self, question, session_id=None):
        if question == "two":
            raise RuntimeError("provider failed")
        return {
            "success": True, "question": question, "sql": "SELECT 1", "answer": "ok",
            "rows": [{"x": 1}], "row_count": 1, "session_id": "s1"
        }


def test_runner_continues_after_agent_exception(tmp_path):
    run = EvaluationRunner(EvaluationAgentAdapter(agent=FakeAgent()), "fake").run(cases(), "dataset")
    assert run.total_cases == 2
    assert run.passed == 1
    assert run.failed == 1
    assert run.cases[1].status == "LLM_ERROR"
    output = tmp_path / "run.json"
    save_run(run, output)
    loaded = load_run(output)
    assert loaded["run_id"] == run.run_id
    assert "Failed Cases" in run_markdown(run)


def test_result_serialization_supports_database_value_types(tmp_path):
    class DatabaseValueAgent:
        def ask(self, question, session_id=None):
            return {
                "success": True,
                "question": question,
                "sql": "SELECT 1",
                "rows": [{"amount": Decimal("12.50"), "day": date(2026, 8, 26)}],
                "row_count": 1,
            }

    run = EvaluationRunner(EvaluationAgentAdapter(agent=DatabaseValueAgent()), "fake").run(
        [CanonicalCase("db", "q", "simple", "easy")], "dataset"
    )
    output = save_run(run, tmp_path / "run.json")
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["cases"][0]["actual"]["rows"][0] == {"amount": "12.50", "day": "2026-08-26"}


def test_filters_cases():
    run = EvaluationRunner(EvaluationAgentAdapter(agent=FakeAgent()), "fake").run(
        cases(), "dataset", case_id="c1", difficulty="easy", limit=1
    )
    assert [item.case_id for item in run.cases] == ["c1"]


def test_comparison_detects_improvement_and_regression():
    a = {
        "run_id": "a", "dataset": "d", "metrics": {"rates": {}, "latency_ms": {}},
        "cases": [{"case_id": "c1", "status": "SUCCESS"}, {"case_id": "c2", "status": "LLM_ERROR"}],
    }
    b = {
        "run_id": "b", "dataset": "d", "metrics": {"rates": {}, "latency_ms": {}},
        "cases": [{"case_id": "c1", "status": "DATABASE_ERROR"}, {"case_id": "c2", "status": "SUCCESS"}],
    }
    comparison = compare_runs(a, b)
    assert comparison["cases_improved"] == ["c2"]
    assert comparison["cases_regressed"] == ["c1"]


def test_follow_up_cases_reuse_session():
    seen = []

    class SessionAgent:
        def ask(self, question, session_id=None):
            seen.append(session_id)
            return {"success": True, "question": question, "sql": "SELECT 1", "rows": [{}], "session_id": "session"}

    followups = [
        CanonicalCase("f1", "first", "conversational_followup", "medium"),
        CanonicalCase("f2", "second", "conversational_followup", "hard"),
    ]
    EvaluationRunner(EvaluationAgentAdapter(agent=SessionAgent()), "fake").run(followups, "d")
    assert seen == [None, "session"]
