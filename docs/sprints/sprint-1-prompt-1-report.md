# Sprint 1 — Prompt 1 Report: Evaluation Harness

## Files created

- `evaluation/models.py`: canonical case, response, case-result and run contracts.
- `evaluation/adapters.py`: list/object dataset adapter and lazy production-Agent adapter.
- `evaluation/metrics.py`: conservative supported metrics and failure classification.
- `evaluation/runner.py`: filtered execution, persistence, Markdown reports and run comparison.
- `evaluation/run.py`: CLI for offline validation, online evaluation and comparison.
- `evaluation/__init__.py` and `tests/test_evaluation/*`.
- `docs/evaluation/current-evaluation-audit.md`, `dataset-schema.md`, `evaluation-runner.md`.
- Offline artifacts under `artifacts/evaluation/`.

## Files modified

- `docs/evaluation/metrics.md` and `evaluation/README.md` updated to describe the executable harness.
- `pyproject.toml` adds the `pytest` development dependency group; `uv.lock` was regenerated.

No production Agent, SQL generation, prompt, validator, memory, schema, API or UI file was changed.

## Tests and verification

- `uv run pytest -q`: **17 passed**.
- `python -m compileall -q evaluation tests/test_evaluation src`: passed.
- Offline validation: smoke dataset **25 cases**, baseline dataset **102 cases**; Agent was not invoked.
- Offline artifacts: `baseline-offline.json/.md`, `final-offline.json/.md`, `final-comparison.json/.md`.
- Runtime integration: skipped; external MSSQL and LLM provider are required and were not simulated.

## Implemented capabilities

Canonical dataset normalization, required-field validation, duplicate-ID detection, lazy Agent adaptation, session propagation for follow-ups, per-case exception isolation, `response_success`, `sql_present`, conservative `execution_success`, `empty_result`, latency statistics, failure categories, JSON/Markdown storage, run comparison and CLI filters (`--limit`, `--case-id`, `--category`, `--difficulty`).

## Known limitations

Execution success is a conservative proxy derived from the current Agent envelope; it is not independent DB evidence. SQL equivalence, schema linking, semantic correctness, answer groundedness, translation quality and column relevance are `not automatically measurable` with current datasets because no gold SQL/result/reviewer labels exist. Offline mode validates and records skipped cases but does not score Agent behavior. UUID run IDs and timestamps vary by design; case selection, normalization and metrics are deterministic.

## Remaining work for Prompt 2

Only after approval: use this harness to establish an online baseline where MSSQL/LLM credentials are available, then implement the next explicitly authorized Sprint 1 safety slice. Do not modify the Agent through this Prompt 1 deliverable.
