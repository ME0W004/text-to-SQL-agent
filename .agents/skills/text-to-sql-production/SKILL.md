---
name: text-to-sql-production
description: Use for any production code work in the Text-to-SQL Agent repository — implementing features, refactoring, bug fixes, integration work, testing, debugging, code review, and sprint/prompt stage execution. Guides scoped engineering, architecture preservation, deterministic validation, regression testing, and evidence-based reporting. Trigger even when the user doesn't explicitly ask for it.
---

# Text-to-SQL Production Engineering Skill

## Mission

Make the smallest safe change required by the requested task while preserving
the approved architecture, security, execution safety, observability,
compatibility, and evaluation guarantees.

## Workflow

### Phase 1 — Understand

Before editing:

1. Read the task carefully.
2. Inspect the relevant source tree.
3. Read relevant ADRs and architecture documentation.
4. Identify affected modules.
5. Identify existing tests covering the behavior.
6. Establish a baseline when the task is non-trivial.

Never edit first and investigate later.

### Phase 2 — Scope

Explicitly determine:

* requested behavior
* affected components
* dependencies
* invariants that must not change
* tests required
* documentation required

Do not add adjacent improvements unless they are required to complete the
requested task safely.

### Phase 3 — Implement

Prefer:

* existing contracts
* existing abstractions
* existing repository tooling
* minimal diffs
* explicit dependencies
* deterministic behavior

Avoid:

* speculative abstractions
* global mutable state
* hidden side effects
* duplicated types
* bypassing established boundaries
* broad refactors during focused tasks

### Phase 4 — Validate

Run targeted tests first.

Then run the appropriate regression suite.

Check:

* functional behavior
* architecture boundaries
* security
* execution safety
* state isolation
* observability
* compatibility

If a test fails:

1. determine the root cause
2. do not hide the failure
3. fix only within task scope
4. rerun the relevant test
5. rerun regression

### Phase 5 — Review

Before declaring completion:

* inspect the diff
* identify unrelated changes
* check imports
* check for duplicate implementations
* check for new cycles
* check logging and secret exposure
* check documentation consistency

### Phase 6 — Report

The final report must be evidence-based.

Never claim:

* tests passed without executing them
* compatibility is preserved without validation
* architecture is enforced without executable checks

Report exact numbers.

Explicitly identify:

* changed files
* tests
* remaining debt
* deviations
* known limitations
* behavior changes

## Hard Safety Rules

Never:

* bypass SQL security
* bypass ExecutionPolicy
* execute unvalidated SQL
* weaken security tests
* leak secrets
* introduce cross-request state
* introduce shared database cursors
* introduce provider SDK imports into protected layers
* mutate AgentState outside the Orchestrator

## Sprint Boundary Rule

Complete only the requested sprint/prompt/stage.

Stop after the required deliverable.

Do not automatically start the next prompt.

## Quality Standard

The preferred solution is not the shortest code.

The preferred solution is the smallest solution that is:

* correct
* testable
* observable
* secure
* compatible
* architecturally consistent
* maintainable
