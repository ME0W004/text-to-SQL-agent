# Evaluation Runner

## Commands

Offline dataset validation (no DB, network or Agent construction):

```bash
python -m evaluation.run --dataset docs/evaluation/initial-dataset.json --offline
python -m evaluation.run --dataset evaluation/baseline_questions.json --offline --limit 20 --category aggregation
```

Production evaluation, when MSSQL and an LLM provider are configured:

```bash
python -m evaluation.run \
  --dataset docs/evaluation/initial-dataset.json \
  --output artifacts/evaluation/run.json \
  --limit 5
```

The runner writes the requested JSON artifact and a same-stem Markdown report. Each case is isolated at the adapter boundary: an Agent exception becomes a classified failed case and does not abort the remaining cases. Follow-up cases in the `conversational_followup` category reuse the returned session ID; other categories start without a session ID.

Compare two stored runs:

```bash
python -m evaluation.run --compare artifacts/a.json artifacts/b.json \
  --comparison-output artifacts/comparison.json
```

Comparison reports only common case IDs and classifies them as improved, regressed or unchanged using the conservative pass statuses `SUCCESS` and `EMPTY_RESULT`. It does not invent semantic correctness scores.

## Output contract

Run JSON includes `run_id`, UTC timestamp, dataset path, agent version, mode, totals, aggregate rates/latency/failure categories and per-case `actual`, `expected`, metrics and failure information. The Markdown report is for human review; JSON is the machine-readable source.

## Current metric semantics

`response_success` reflects the Agent response envelope. `sql_present` checks that a non-empty SQL string was returned. `execution_success` is a conservative proxy based on a successful Agent response with SQL present; it is not an independent database proof. Empty results are a successful terminal state and are classified as `EMPTY_RESULT`. SQL/semantic/answer correctness remains `not automatically measurable` without gold evidence.
