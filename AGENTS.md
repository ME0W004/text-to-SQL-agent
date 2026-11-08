# AGENTS.md

## Project Identity

This repository is a production-grade Text-to-SQL Agent.

The project is developed incrementally through controlled architectural
sprints. Correctness, safety, maintainability, observability, evaluation,
and backward compatibility are more important than speed of implementation.

## Primary Engineering Principles

1. Inspect before modifying.
2. Read the relevant architecture documentation and ADRs before making
   architectural changes.
3. Preserve approved architecture unless a concrete contradiction or defect
   is proven.
4. Prefer the smallest safe change.
5. Do not introduce speculative abstractions.
6. Do not perform unrelated cleanup during scoped tasks.
7. Never claim a test passed unless it was actually executed.
8. Never hide failures, warnings, or skipped tests.
9. Every production change must have appropriate tests.
10. Every completed task must include an evidence-based engineering report.

## Sprint Discipline

Development is organized into explicit sprint and prompt stages.

The Agent MUST:

* work only on the requested stage
* stop when the requested stage is complete
* produce the required report
* wait for explicit authorization before starting the next stage

The Agent MUST NOT automatically continue into the next prompt or sprint.

If implementation discovers that the approved architecture is materially
wrong, stop and report the discrepancy instead of silently redesigning it.

## Architecture Rules

### AgentState

AgentState is owned by the application orchestration layer.

ONLY the Orchestrator may write AgentState.

Components must communicate through typed contracts/results rather than
mutating AgentState directly.

Do not introduce new AgentState mutation paths.

### Contracts

`src/agents/contracts.py` is the authoritative contract surface unless an
approved ADR explicitly changes that decision.

Do not duplicate Protocols, Value Objects, errors, or canonical shared types.

Compatibility modules may re-export canonical definitions, but must not define
duplicate implementations.

### Database Boundary

Database access is isolated behind the approved database execution boundary.

QueryExecutor / DatabaseConnection are responsible for crossing into the
database driver layer.

Do not import `pymssql` into lightweight/domain/contract modules.

Do not bypass Security Core or ExecutionPolicy.

### LLM Boundary

LLM provider SDKs belong behind the approved LLM/provider boundary.

Do not add direct provider imports to domain, contracts, state, or unrelated
application components.

Use the approved LLM abstraction once available.

### Security

The deterministic SQL Security Core is authoritative.

Never bypass it.

Never weaken validation to make a query pass.

Never add a runtime path that executes SQL before security validation.

Security changes require explicit tests.

### Execution Safety

ExecutionPolicy is mandatory for database execution.

Preserve:

* query timeout
* connection timeout
* row limits
* fetch batching
* overflow handling
* typed errors
* connection/cursor lifecycle

Do not silently relax execution safety.

### Observability

Preserve:

* TraceContext
* structured logging
* SQL fingerprinting
* sanitization
* trace propagation

Never log:

* raw secrets
* credentials
* API keys
* raw prompts when sensitive
* raw model responses when sensitive
* raw SQL unless explicitly approved and sanitized
* full result rows unless explicitly required and safe

## Dependency Rules

Prefer dependency direction:

domain/value objects
↓
contracts
↓
application/orchestration
↓
adapters/infrastructure
↓
external providers

External providers MUST NOT leak upward into domain or contracts.

Avoid dependency cycles.

Avoid hidden global state.

Avoid import-time initialization of:

* databases
* network clients
* LLM clients
* API servers
* UI frameworks
* request-specific state

## Compatibility

Preserve existing public behavior unless a change is explicitly approved.

Important compatibility surfaces include:

* `/api/v1/ask`
* AskRequest
* AskResponse
* TextToSQLAgent
* execute_query()
* CLI entrypoints
* evaluation harness
* existing session behavior

When changing imports or moving implementations:

* keep canonical ownership singular
* use compatibility re-exports where appropriate
* identity-test canonical and compatibility objects

## Testing Requirements

Before completion, run relevant targeted tests.

Before final report, run the appropriate regression suite.

At minimum consider:

* unit tests
* architecture tests
* security tests
* database/execution tests
* observability tests
* evaluation tests
* integration tests
* API tests
* CLI tests
* compile/import validation

Use repository-native tooling first.

Do not introduce a new testing framework without a concrete reason.

## Documentation Requirements

Architectural changes require corresponding documentation.

Use ADRs for material architecture decisions.

Documentation must reflect actual implementation.

Never document an architecture rule as enforced unless an executable test
or validation mechanism protects it.

## Change Discipline

For every changed production file, know:

* why it changed
* which requirement requires it
* what behavior is expected to remain unchanged
* which tests validate it

Prefer small, reviewable commits/changesets.

Do not modify unrelated files.

## Final Report Requirements

Every completed scoped task must report:

1. What was changed.
2. Why it was changed.
3. Files changed.
4. Tests actually executed.
5. Exact test results.
6. Known limitations.
7. Remaining migration debt.
8. Deviations from the approved design.
9. Whether runtime behavior changed.
10. Whether public API changed.
11. Whether security behavior changed.
12. Whether database execution behavior changed.
13. Whether AgentState ownership changed.
14. Whether observability behavior changed.

Use PASS / PASS WITH DEBT / BLOCKED as the final status.

## Stop Conditions

Stop and report instead of improvising when:

* a security boundary would be weakened
* a public API would break unexpectedly
* a new dependency cycle appears
* an approved architectural invariant cannot be preserved
* baseline tests unexpectedly fail
* production behavior would need to change outside scope
* the task would require speculative redesign

## Project-Specific Development Philosophy

This project is optimized for:

* correctness over cleverness
* explicit contracts over implicit behavior
* deterministic validation over prompt-only safeguards
* bounded execution over unlimited execution
* evaluated quality over subjective quality
* incremental architecture over large rewrites
* evidence-based engineering over assumptions

The Agent must optimize for long-term reliability of the Text-to-SQL system,
not merely for completing the current coding task quickly.
