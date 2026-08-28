# Metric Definitions

The metric catalog and pass criteria are in evaluation-strategy.md. The executable harness currently implements only response-envelope, SQL-presence, conservative execution-proxy, empty-result, latency and failure-category metrics. Every score must include dataset version, schema snapshot, provider/model and comparison method (exact SQL, execution equivalence or reviewer score). SQL semantic correctness, schema linking, answer correctness and groundedness are `not automatically measurable` without gold evidence. Unknown behavior is recorded as unresolved, never guessed.
