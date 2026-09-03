# ADR-015: Typed Error Code Registry

Status: Accepted.

Decision: All target components raise errors from the typed hierarchy in
`docs/contracts/interface-design.md` §7, with codes from the canonical
code registry in §7.4. New codes must be added to the registry before any
component uses them. Internal exception text MUST NOT cross a component
boundary.

Rationale: typed codes are stable for monitoring, alerting, and
orchestration retry logic. Raw exception text is unsafe to expose and
changes between Python releases. Sprint 1 introduced typed errors at the
database boundary (`DatabaseError` + `DatabaseErrorCode`) and at the
security boundary (`SQLSecurityError` + `SecurityReasonCode`). This ADR
extends the typed-error principle to every new component.

Consequence: future components MUST raise errors from the documented
hierarchy. New error codes MUST be added to the registry in
`interface-design.md` §7.4 before any component uses them, and must be
referenced by ADR. Backward compatibility with existing
`SQLValidationError`, `SQLSecurityError`, `QueryExecutionError`,
`DatabaseError` is preserved.