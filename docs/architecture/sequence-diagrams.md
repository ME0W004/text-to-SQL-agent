# Sequence Diagrams

## Current request

See `current-data-flow.md`: UI/API call the singleton Agent; memory and LLM calls happen synchronously; one shared database cursor executes an unbounded fetch.

## Target request

See `target-data-flow.md`: typed analysis and planning precede generation; every repair re-enters deterministic validation; only the guarded executor reaches MSSQL; result/profile/presentation stages precede grounded answer validation.

## Boundary rule

Adapters (CLI, API, UI) may translate transport concerns but must not own SQL, schema policy or database execution. The target Agent coordinates use cases while each core component enforces its contract.
