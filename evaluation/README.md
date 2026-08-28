# Baseline Evaluation — README

The executable harness is documented in `docs/evaluation/evaluation-runner.md`. Validate datasets offline with `python -m evaluation.run --dataset evaluation/baseline_questions.json --offline`; no Agent, database or provider is invoked in this mode.

## Purpose

`baseline_questions.json` is the Sprint 0 **baseline corpus**: a fixed set of 102 Persian questions that represent what this Text-to-SQL agent is expected to handle against the real `AgentiveAi.Sale` database. It exists to make future sprints measurable: every refactor of prompts, validation, memory, or presentation can be re-run against these cases and compared.

This is a baseline, not a leaderboard: the current system is *expected to fail* a large share of cases (see `expected-behavior.md` and the gap analysis). The value is in recording where it stands today so progress becomes demonstrable.

## Dataset composition

| Category | Cases | What they probe |
|---|---|---|
| simple_lookup | q001–q006 | basic projections, DISTINCT, schema awareness (incl. Shipments) |
| filtering | q007–q014 | WHERE logic, NULL/zero traps, enum values |
| aggregation | q015–q022 | SUM/AVG/COUNT semantics, unit & currency hazards |
| grouping | q023–q029 | GROUP BY dimensions incl. jalali month/year columns |
| ranking | q030–q035 | ordering, ties, fuzzy segmentation |
| top_n | q036–q040 | TOP-N patterns from few-shots and beyond |
| date_time | q041–q047 | entityDate vs operationMonth vs jalaliYearNo; impossible dates |
| comparison | q048–q051 | period/entity comparisons, derived deltas |
| join | q052–q057 | inner/left/anti-joins over the real FK chain |
| multi_table | q058–q062 | three-table chains, delivery ratios (real historical asks) |
| calculation | q063–q068 | VAT math, currency conversion, per-unit prices |
| empty_result | q069–q074 | honest empty handling, guidance quality |
| conversational_followup | q075–q079 | ellipsis/coreference across turns (incl. verbatim historical failure q078) |
| ambiguous | q080–q084 | underspecified requests; assumption disclosure |
| adversarial | q085–q088 | off-domain, destructive intent, hallucination bait |
| security_sensitive | q089–q094 | injection/multi-statement/exhaustion/XSS probes (regression sentinels) |
| column_selection | q095–q098 | projection discipline, ID suppression |
| interpretation | q099–q102 | grounded narrative analytics |

Difficulty mix: ~35 easy · ~40 medium · ~27 hard (assigned per case).

## How cases should be executed

1. Bring up the API (`uv run python run_api.py`) with valid `.env`; UI optional.
2. For each case: POST `/api/v1/ask` with `{question, session_id}`.
   - Follow-up cases (q075–q079) must run in order within one session_id.
   - Security cases should run against a **disposable database copy** wherever possible; q090/q092 especially must never run against production data until the bypass is fixed.
3. Capture for each response: `success`, `sql`, `row_count`, first rows, `answer`.
4. Score per `evaluation/expected-behavior.md` category rules (binary pass/fail + notes field).
5. Record results in a dated artifact (e.g., `evaluation/runs/run_YYYYMMDD.json`) — format intentionally left open until the observability sprint defines it.

## What should be evaluated

- **Correctness signal**: does the SQL target the right tables/columns/filters (per case `notes`)?
- **Safety**: was anything non-SELECT executed? Was any destructive statement blocked?
- **Honesty**: are empty results reported truthfully; are assumptions disclosed on ambiguous cases?
- **Presentation**: Persian headers, no raw English aliases where avoidable, consistent numbers between narrative and table.
- **Robustness**: no crashes/timeouts; session continuity on follow-ups.

## Limitations

- No automated runner exists yet (Sprint 0 scope forbade building one); execution is manual/scripted ad hoc.
- Expected behaviors define acceptance signals, not exact SQL — grading has a human-judgment component until an LLM-as-judge or deterministic checker sprint lands.
- Some "hard" cases (e.g., q032 destination segmentation, q083 identifier disambiguation) admit multiple defensible answers; graders must accept any answer that discloses its assumption.
- Row-level ground truths (exact counts like «13 rows» for q060) reflect the database snapshot at Sprint 0 (Aug 2026); re-verify if data changes.
- Security cases marked TARGET describe post-fix behavior; current known-fail status is documented rather than hidden.

## Known unanswered / expected-to-fail cases under the current system

Based strictly on code audit (not yet executed):

- q006/q019/q034/q040/q054/q056/q097 — require Shipments columns absent from the generation prompt.
- q046 — few-shot actively teaches the impossible year '1403'.
- q018 — historical evidence shows AVG(quantity) confusion (report.md).
- q059/q060/q078 — delivery-ratio reasoning; q078 failed historically (stored conversation).
- q090/q091/q092 — validator gaps proven empirically.
- q094 — unsafe HTML rendering path.
- q068 — currency conversion unused anywhere today.
- q101 — constraint introspection doesn't exist (no constraints to introspect).
