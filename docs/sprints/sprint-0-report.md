# Sprint 0 Report

## Executive Summary

The repository is a functioning MVP, not a production-grade architecture. Its real center is one OpenAI-compatible LLM round trip surrounded by thin prompt, validation, DB, memory and UI layers. Existing audits were reconciled with source code; this Sprint adds canonical current/target architecture, contracts, ADRs, evaluation and migration documents without runtime refactor.

## Current Architecture and Flow

See architecture/current-architecture.md, current-components.md and current-data-flow.md. In brief: Streamlit or CLI -> FastAPI/singleton Agent -> SQLite history -> SQLExtractor/OpenAI -> prefix/blocklist/sqlglot -> shared pymssql fetchall -> Id filtering/static Persian map -> optional answer LLM -> response.

## Audit Summary

Security, memory, prompts, output and problem details are in docs/audit/*. Critical findings are multi-statement bypass and unbounded execution. High findings include schema drift, broken column-map regeneration, shared cursor/singleton, absent semantic validation, unsafe HTML and no regression harness. README claims of provider fallback and broad auto-repair exceed implementation.

## Target Architecture, Contracts and ADRs

Target components, non-responsibilities and flow are documented under docs/architecture and docs/contracts. ADR-001..011 record separation of understanding/generation, schema intelligence, planning, deterministic security, semantic validation, safe execution, result analysis, presentation planning, layered memory, continuous evaluation and routing.

## Evaluation and Dataset

evaluation/baseline_questions.json contains 102 grounded cases; docs/evaluation/initial-dataset.json is a separate 25-case smoke dataset. Metrics and pass criteria are in docs/evaluation/evaluation-strategy.md. Gold SQL is intentionally semantic and unresolved cases are not guessed.

## Migration

docs/migration/migration-plan.md defines seven rollback-capable phases preserving /api/v1/ask. Recommended Sprint 1: implement evaluation harness plus deterministic security/read-only execution guard, typed errors, request tracing and row/time caps. Do not begin Sprint 1 without approval.

## Risks and Open Questions

DB permissions, deployment concurrency, production data drift, provider relay behavior and exact semantic definitions for currency/unit/export metrics require confirmation. Secrets in .env.example must be replaced/rotated.

## Definition of Done

The requested Sprint 0 outputs are complete:

- [x] Repository/code/configuration/documentation audit
- [x] Current architecture, components and data flow
- [x] Dependency/coupling analysis; no circular import found
- [x] Prompt, security, memory and output audits
- [x] Evidence-based problem inventory with severity and sprint targets
- [x] Target architecture, target flow and component responsibilities
- [x] Contracts with inputs, outputs, errors, invariants and non-responsibilities
- [x] ADR-001 through ADR-011
- [x] Evaluation strategy and metric definitions
- [x] 25-question machine-readable smoke dataset (`docs/evaluation/initial-dataset.json`)
- [x] 102-question broader baseline corpus retained in `evaluation/baseline_questions.json`
- [x] Incremental, rollback-capable migration plan
- [x] This final Sprint 0 report
- [x] No unnecessary refactor, prompt rewrite or behavior change

## Files Created

Architecture: `docs/architecture/current-architecture.md`, `current-components.md`, `current-data-flow.md`, `target-architecture.md`, `target-data-flow.md`, `component-responsibilities.md`, `sequence-diagrams.md`.

Audits: `docs/audit/codebase-audit.md`, `dependency-coupling.md`, `security-audit.md`, `memory-audit.md`, `prompt-audit.md`, `output-audit.md`, `problem-inventory.md`.

Contracts/decisions/evaluation/migration: `docs/contracts/component-contracts.md`, `docs/adr/ADR-001..011`, `docs/evaluation/evaluation-strategy.md`, `metrics.md`, `initial-dataset.md`, `initial-dataset.json`, and `docs/migration/migration-plan.md`.

## Code Changes

None. No Python, configuration, prompt or runtime behavior was changed. Existing application files were preserved.

## Verification

- `python -m compileall -q src run.py run_api.py run_ui.py`: passed.
- JSON parsing for both evaluation datasets: passed.
- Smoke dataset validation: 25 cases; all required fields present.
- `pytest --collect-only -q`: no tests collected (the repository contains only empty test package initializers).
- Runtime/API/database smoke test: not run because it requires reachable external MSSQL and LLM provider services; this is recorded as an environment prerequisite, not a code change.

## Documentation vs Code

The authoritative discrepancy list remains `docs/current-system/discrepancies.md`. The most consequential differences are unwired provider fallback, validation-only repair, incomplete Shipments prompt schema, broken relative-path map regeneration, concrete values in `.env.example`, and unsupported production-readiness claims in `report.md`.

## Recommended Sprint 1

Implement the evaluation harness and the first safety slice: deterministic AST single-statement/read-only validation, typed database errors, request tracing, and row/time execution caps behind feature flags. Add focused tests before introducing planning or prompt changes. Do not begin Sprint 1 without explicit approval.
