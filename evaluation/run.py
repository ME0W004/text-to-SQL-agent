"""CLI entry point: ``python -m evaluation.run``."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from .adapters import EvaluationAgentAdapter, load_dataset
from .models import CaseResult, EvaluationRun, utc_now
from .runner import EvaluationRunner, comparison_markdown, load_run, run_markdown, save_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or compare Text-to-SQL evaluations.")
    parser.add_argument("--dataset", help="JSON dataset path (list or {cases: [...]})")
    parser.add_argument("--output", help="JSON output path; Markdown is written beside it")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id")
    parser.add_argument("--category")
    parser.add_argument("--difficulty", choices=("easy", "medium", "hard"))
    parser.add_argument("--offline", action="store_true", help="Validate dataset without invoking Agent")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--agent-version", default="current", help="Version label stored in run metadata")
    parser.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), help="Compare two JSON run artifacts")
    parser.add_argument("--comparison-output", help="JSON path for comparison output")
    return parser


def _markdown_path(json_path: Path) -> Path:
    return json_path.with_suffix(".md")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.compare:
        from .runner import compare_runs
        comparison = compare_runs(load_run(args.compare[0]), load_run(args.compare[1]))
        output = Path(args.comparison_output or "comparison.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        import json
        output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        output.with_suffix(".md").write_text(comparison_markdown(comparison), encoding="utf-8")
        print(f"Comparison written to {output} and {output.with_suffix('.md')}")
        return 0

    if not args.dataset:
        print("--dataset is required unless --compare is used.")
        return 2
    cases = load_dataset(args.dataset)
    if args.offline:
        selected = cases
        if args.case_id:
            selected = [case for case in selected if case.id == args.case_id]
        if args.category:
            selected = [case for case in selected if case.category == args.category]
        if args.difficulty:
            selected = [case for case in selected if case.difficulty == args.difficulty]
        if args.limit is not None:
            selected = selected[:args.limit]
        skipped_cases = [
            CaseResult(
                case_id=case.id,
                question=case.question,
                status="SKIPPED",
                failure_category=None,
                metrics={"dataset_valid": True},
                actual={},
                expected={
                    "intent": case.expected_intent,
                    "tables": case.expected_tables,
                    "columns": case.expected_columns,
                    "behavior": case.expected_behavior,
                    "gold_sql": case.gold_sql,
                    "notes": case.notes,
                },
                failure="offline mode: Agent/database/provider not invoked",
                latency_ms=None,
            )
            for case in selected
        ]
        run = EvaluationRun(
            run_id=uuid.uuid4().hex,
            timestamp=utc_now(),
            dataset=args.dataset,
            agent_version="not-invoked",
            total_cases=len(skipped_cases),
            passed=0,
            failed=0,
            skipped=len(skipped_cases),
            metrics={"validation": {"dataset_valid": True, "cases": len(skipped_cases)}},
            cases=skipped_cases,
            mode="offline",
        )
        if args.output:
            output = Path(args.output)
            save_run(run, output)
            _markdown_path(output).write_text(run_markdown(run), encoding="utf-8")
            print(f"Offline artifact written to {output} and {_markdown_path(output)}")
        print(f"Offline dataset validation passed: {len(selected)} case(s); Agent was not invoked.")
        return 0

    try:
        from src.agents.text_to_sql_agent import TextToSQLAgent
    except Exception as exc:
        print(f"Unable to import production Agent: {exc}")
        return 2
    adapter = EvaluationAgentAdapter(agent_factory=TextToSQLAgent, agent_version=args.agent_version)
    try:
        run = EvaluationRunner(adapter, agent_version=adapter.agent_version).run(
            cases,
            dataset=args.dataset,
            limit=args.limit,
            case_id=args.case_id,
            category=args.category,
            difficulty=args.difficulty,
        )
    finally:
        adapter.close()
    output = Path(args.output or f"evaluation_run_{run.run_id}.json")
    save_run(run, output)
    _markdown_path(output).write_text(run_markdown(run), encoding="utf-8")
    print(f"Run written to {output} and {_markdown_path(output)}")
    print(f"Cases: {run.total_cases}; passed: {run.passed}; failed: {run.failed}")
    if args.verbose:
        for case in run.cases:
            print(f"{case.case_id}: {case.status} ({case.latency_ms} ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
