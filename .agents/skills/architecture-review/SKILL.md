---
name: architecture-review
description: Use when auditing architecture, dependencies, package boundaries, ADRs, contracts, state ownership, provider boundaries, database boundaries, or proposed structural changes in the Text-to-SQL Agent repository. Use before or during architectural refactors, sprint architecture audits, dependency reviews, and design validation — trigger even when the user doesn't explicitly ask for an "architecture review".
---

# Architecture Review Skill

## Mission

Evaluate architecture using evidence from the repository rather than assumptions.

## Review Order

1. Read relevant ADRs.
2. Inspect actual source dependencies.
3. Inspect tests enforcing current rules.
4. Map responsibilities.
5. Identify actual violations.
6. Distinguish intentional debt from defects.
7. Propose the smallest architecture change that solves the demonstrated
   problem.
8. Define executable enforcement.
9. Define migration/compatibility requirements.
10. Stop before implementation unless implementation was explicitly requested.

## Required Classifications

Every finding must be classified as one of:

* Critical
* High
* Medium
* Low
* Intentional
* Migration Debt

Never describe a hypothetical concern as a confirmed violation.

## Dependency Analysis

Review:

* direct imports
* transitive imports
* dependency cycles
* provider leakage
* database leakage
* API/UI leakage
* memory leakage
* state leakage
* global state
* import-time side effects

When relevant, prefer both:

* static analysis
* runtime/subprocess verification

## Contract Analysis

Verify:

* one canonical definition per important type
* Protocol ownership
* Value Object ownership
* compatibility re-exports
* absence of duplicated implementations

## State Analysis

Verify:

* single-writer ownership
* request isolation
* no hidden mutation
* no component-level AgentState mutation
* no shared mutable state

## Boundary Analysis

Verify:

* database access boundary
* LLM provider boundary
* security boundary
* application/interface boundary
* memory boundary

## Architecture Proposal Requirements

A proposed design should include:

* target structure
* dependency direction
* ownership rules
* migration strategy
* compatibility strategy
* enforcement tests
* known trade-offs

Do not create layers merely for aesthetic symmetry.

## Evidence Standard

Every major claim should have one of:

* source inspection evidence
* executable test evidence
* runtime probe evidence
* explicit documentation/ADR evidence

Clearly distinguish evidence from inference.

## Output

For architecture audits, report:

1. Executive summary
2. Current state
3. Confirmed findings
4. Intentional exceptions
5. Migration debt
6. Target design
7. Dependency rules
8. Enforcement strategy
9. Compatibility impact
10. Risks
11. Recommended implementation sequence

Do not silently implement after an audit-only request.
