# ADR-012: Component Contract Authority

Status: Accepted.

Decision: The interface contracts documented in
`docs/contracts/component-contracts.md` and
`docs/architecture/dependency-rules.md` are the authoritative source for
target component responsibilities, inputs, outputs, errors, side effects,
dependencies, state interaction, determinism and security sensitivity.

Any future implementation MUST satisfy these contracts. Any deviation
(adding a dependency, widening a contract, removing a non-responsibility)
requires either updating the contract documents together with an ADR, or
a separate ADR that supersedes this one.

Consequence: future prompts that build new components must update both
the contract document and this ADR (or write a successor ADR) when the
contract changes. The contract documents are living references, not
historical records.