"""Evaluation execution, persistence and run comparison."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .adapters import EvaluationAgentAdapter
from .metrics import PASS_STATUSES, aggregate_metrics, case_metrics, classify_failure
from .models import CaseResult, CanonicalCase, EvaluationRun, EvaluationResponse, utc_now


def _expected(case: CanonicalCase) -> dict:
    return {
        "intent": case.expected_intent,
        "tables": case.expected_tables,
        "columns": case.expected_columns,
        "behavior": case.expected_behavior,
        "gold_sql": case.gold_sql,
        "notes": case.notes,
    }


class EvaluationRunner:
    def __init__(self, adapter: EvaluationAgentAdapter, agent_version: str = "unknown"):
        self.adapter = adapter
        self.agent_version = agent_version

    def run(
        self,
        cases: list[CanonicalCase],
        dataset: str,
        limit: Optional[int] = None,
        case_id: Optional[str] = None,
        category: Optional[str] = None,
        difficulty: Optional[str] = None,
        mode: str = "online",
    ) -> EvaluationRun:
        selected = cases
        if case_id:
            selected = [case for case in selected if case.id == case_id]
        if category:
            selected = [case for case in selected if case.category == category]
        if difficulty:
            selected = [case for case in selected if case.difficulty == difficulty]
        if limit is not None:
            selected = selected[:limit]

        results: list[CaseResult] = []
        session_id: Optional[str] = None
        session_category: Optional[str] = None
        for case in selected:
            # Follow-up cases are intentionally grouped by their existing category;
            # unrelated cases receive a fresh session to avoid context pollution.
            current_session = session_id if case.category == "conversational_followup" else None
            if case.category != session_category and case.category != "conversational_followup":
                session_id = None
            try:
                response = self.adapter.ask(case.question, current_session)
                failure_category = classify_failure(response)
                metrics = case_metrics(response, failure_category)
                actual = response.to_dict()
                if response.session_id and case.category == "conversational_followup":
                    session_id = response.session_id
                    session_category = case.category
                result = CaseResult(
                    case_id=case.id,
                    question=case.question,
                    status=failure_category,
                    failure_category=None if failure_category in PASS_STATUSES else failure_category,
                    metrics=metrics,
                    actual=actual,
                    expected=_expected(case),
                    failure=response.error if failure_category not in PASS_STATUSES else None,
                    latency_ms=response.latency_ms,
                )
            except Exception as exc:  # Defensive runner boundary.
                failure_category = classify_failure(None, exc)
                result = CaseResult(
                    case_id=case.id,
                    question=case.question,
                    status=failure_category,
                    failure_category=failure_category,
                    metrics={"response_success": False, "sql_present": False, "execution_success": False},
                    actual={},
                    expected=_expected(case),
                    failure=f"{type(exc).__name__}: {exc}",
                    latency_ms=None,
                )
            results.append(result)

        result_dicts = [result.to_dict() for result in results]
        passed = sum(result.status in PASS_STATUSES for result in results)
        return EvaluationRun(
            run_id=uuid.uuid4().hex,
            timestamp=utc_now(),
            dataset=dataset,
            agent_version=self.agent_version,
            total_cases=len(results),
            passed=passed,
            failed=len(results) - passed,
            metrics=aggregate_metrics(result_dicts),
            cases=results,
            mode=mode,
            skipped=0,
        )


def save_run(run: EvaluationRun, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(run.to_dict(), ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return path


def _json_default(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def load_run(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_runs(run_a: dict, run_b: dict) -> dict:
    cases_a = {case["case_id"]: case for case in run_a.get("cases", [])}
    cases_b = {case["case_id"]: case for case in run_b.get("cases", [])}
    common = sorted(cases_a.keys() & cases_b.keys())
    improved, regressed, unchanged = [], [], []
    for case_id in common:
        a_pass = cases_a[case_id].get("status") in PASS_STATUSES
        b_pass = cases_b[case_id].get("status") in PASS_STATUSES
        if b_pass and not a_pass:
            improved.append(case_id)
        elif a_pass and not b_pass:
            regressed.append(case_id)
        else:
            unchanged.append(case_id)

    def overall(run: dict, name: str) -> Optional[float]:
        return run.get("metrics", {}).get("rates", {}).get(name)

    return {
        "run_a": {"run_id": run_a.get("run_id"), "dataset": run_a.get("dataset")},
        "run_b": {"run_id": run_b.get("run_id"), "dataset": run_b.get("dataset")},
        "common_cases": len(common),
        "overall": {
            "response_success": {"a": overall(run_a, "response_success"), "b": overall(run_b, "response_success")},
            "sql_present": {"a": overall(run_a, "sql_present"), "b": overall(run_b, "sql_present")},
            "execution_success": {"a": overall(run_a, "execution_success"), "b": overall(run_b, "execution_success")},
            "mean_latency_ms": {
                "a": run_a.get("metrics", {}).get("latency_ms", {}).get("mean_ms"),
                "b": run_b.get("metrics", {}).get("latency_ms", {}).get("mean_ms"),
            },
        },
        "cases_improved": improved,
        "cases_regressed": regressed,
        "cases_unchanged": unchanged,
        "counts": {"improved": len(improved), "regressed": len(regressed), "unchanged": len(unchanged)},
    }


def comparison_markdown(comparison: dict) -> str:
    overall = comparison["overall"]
    lines = [
        "# Evaluation Run Comparison",
        "",
        f"Run A: `{comparison['run_a'].get('run_id')}`  ",
        f"Run B: `{comparison['run_b'].get('run_id')}`  ",
        f"Common cases: {comparison['common_cases']}",
        "",
        "| Metric | Run A | Run B |",
        "|---|---:|---:|",
    ]
    for key, values in overall.items():
        lines.append(f"| {key} | {values.get('a')} | {values.get('b')} |")
    lines.extend([
        "",
        f"Improved: {comparison['counts']['improved']}",
        f"Regressed: {comparison['counts']['regressed']}",
        f"Unchanged: {comparison['counts']['unchanged']}",
        "",
        "## Regressions",
        "",
        ", ".join(f"`{case_id}`" for case_id in comparison["cases_regressed"]) or "None",
    ])
    return "\n".join(lines) + "\n"


def run_markdown(run: EvaluationRun) -> str:
    failure_counts = run.metrics.get("failure_categories", {})
    lines = [
        "# Evaluation Run",
        "",
        f"Run: `{run.run_id}`  ",
        f"Dataset: `{run.dataset}`  ",
        f"Mode: `{run.mode}`  ",
        f"Agent version: `{run.agent_version}`",
        "",
        f"Cases: {run.total_cases} | Passed: {run.passed} | Failed: {run.failed} | Skipped: {run.skipped}",
        "",
        "## Metrics",
        "",
        f"- Response success: {run.metrics.get('rates', {}).get('response_success')}",
        f"- SQL present: {run.metrics.get('rates', {}).get('sql_present')}",
        f"- Execution success: {run.metrics.get('rates', {}).get('execution_success')}",
        f"- Mean latency (ms): {run.metrics.get('latency_ms', {}).get('mean_ms')}",
        "",
        "## Failure Categories",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(failure_counts.items()))
    lines.extend(["", "## Failed Cases", ""])
    failed = [case for case in run.cases if case.status not in PASS_STATUSES and case.status != "SKIPPED"]
    lines.extend(f"- `{case.case_id}` [{case.status}] {case.failure or ''}" for case in failed)
    if not failed:
        lines.append("None")
    return "\n".join(lines) + "\n"
