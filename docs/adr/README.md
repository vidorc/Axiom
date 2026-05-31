# Architecture Decision Records

This directory holds Axiom's ADRs — the decisions that are **expensive to reverse**. If a choice can be changed in an afternoon, it does not belong here; it belongs in code or a regular doc. An ADR exists so that six months from now, when someone asks "why didn't we just use Temporal?", the answer is written down with the context and trade-offs that were true at decision time.

## Conventions

- **Status** is one of: `Proposed`, `Accepted`, `Superseded by ADR-XXXX`, `Deprecated`.
- ADRs are **immutable once Accepted.** To change a decision, write a new ADR that supersedes the old one — never edit the original's decision. (Typo/link fixes are fine.)
- Each ADR states the decision, the forces, the alternatives we rejected, and the consequences — *including* the ones we don't like.
- Numbering is sequential and permanent.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-postgres-redis-over-temporal.md) | PostgreSQL + Redis custom engine over Temporal/Celery | Accepted |
| [0002](0002-modular-monolith.md) | Modular monolith over microservices | Accepted |
| [0003](0003-python-first.md) | Python-first backend | Accepted |
| [0004](0004-http-manifest-nodes.md) | HTTP-manifest as the primary node type | Accepted |
| [0005](0005-agencies-as-initial-wedge.md) | Agencies + Clay-cost refugees as the initial wedge | Accepted |
| [0006](0006-ecosystem-as-moat.md) | Ecosystem + data gravity as the moat (not MCP/BYOK) | Accepted |
| [0007](0007-licensing-apache-dco.md) | Apache-2.0 core with DCO; open-core for cloud | Accepted |

## Template

New ADRs follow `NNNN-kebab-case-title.md` using the structure below:

```
# ADR-NNNN: Title

- Status: Proposed | Accepted | Superseded by ADR-XXXX
- Date: YYYY-MM-DD
- Deciders: <roles>
- Supersedes / Superseded by: <links, if any>

## Context
What forces are at play? What constraints, what scale, what team?

## Decision
The choice, stated in one or two sentences. Active voice.

## Alternatives considered
Each rejected option and the specific reason it lost.

## Consequences
### Positive
### Negative / accepted costs
### Revisit triggers — the observable signals that should make us reopen this ADR.
```
