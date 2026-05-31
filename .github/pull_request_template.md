<!-- Thanks for contributing to Axiom. Keep PRs focused and small. -->

## What & why

<!-- What does this change do, and why? Link any issue. -->

## How it was verified

<!-- Tests added/run. `make check` should pass. -->

- [ ] `make check` passes locally (fmt · lint · typecheck · boundaries · unit)
- [ ] Tests added or updated for the change
- [ ] `make test-integration` run if this touches the data/transport layer

## Checklist

- [ ] Commits are signed off (`git commit -s`) — DCO, not a CLA (CONTRIBUTING.md)
- [ ] No new cross-module import that violates the boundary rules (ADR-0002)
- [ ] If this contradicts a committed ADR, it includes a superseding ADR with rationale
- [ ] Security checklist satisfied if this touches credentials, tenancy, egress, or
      untrusted input (SECURITY.md §12)
- [ ] No secrets, keys, or `.env` committed

## ADR impact

<!-- Does this change a decision in docs/adr/? If so, which, and is there a superseding ADR? -->
