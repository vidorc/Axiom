# ADR-0002: Modular monolith over microservices

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founding CTO, Principal Architect
- **Related:** `BLUEPRINT.md` §3.1, §4

## Context

The original brief specified a service-per-concern layout (`apps/{web,api,worker}` deployed as separate services, plus six packages). We must choose a deployment topology for a pre-PMF product built by a small team, where **self-host simplicity is a product feature** and the domain boundaries are still being discovered through real usage.

The forces:

- **Team size:** single-digit engineers. Every network boundary is debugging surface, deployment surface, and version-skew surface that a small team pays for continuously.
- **Boundaries are hypotheses.** Pre-PMF, we do not yet know where the true seams are. Microservices freeze boundary guesses into network contracts that are painful to move later — the worst time to commit to a boundary is before you understand it.
- **Self-host:** more services = more to orchestrate = a higher barrier to the OSS adoption we depend on.
- **The natural runtime split is real, though.** The API (request/response) and the worker (long-running execution) genuinely have different scaling and lifecycle profiles. That one split is worth making.

## Decision

We ship a **modular monolith**: a single codebase and a single deployable image, run in two process modes — **`api`** (FastAPI) and **`worker`** (orchestrator + node runtime). Domains are separated as **logical modules** with explicit interfaces and **import-linting enforced** dependency rules, not as network services.

We keep the brief's monorepo package structure — but the packages are *modules in one deployable*, not independently deployed services.

## Alternatives considered

### Full microservices (per the brief) — rejected
Premature distribution. We would pay distributed-systems costs — partial failure, network serialization, distributed tracing, per-service CI/CD, version skew, local-dev orchestration — *before* having the scale or team that justifies them. Worse, it would force us to commit to domain boundaries we haven't validated. Conway's Law in reverse: a 4-person team running 9 services spends its time on the seams, not the product.

### Single unstructured monolith (a "big ball of mud") — rejected
The opposite failure: no internal boundaries, so the codebase becomes impossible to reason about and *impossible to later extract* into services when we genuinely need to. We want the option to split later, which requires the seams to exist now.

### Serverless functions — rejected
Wrong fit for a stateful, long-running orchestrator with worker leases and live WebSocket streams. Cold starts and execution-time limits fight the workload, and it complicates self-host enormously.

## What "modular" requires (the discipline that makes this work)

A modular monolith is only valuable if the modularity is *enforced*. Otherwise it silently degrades into a big ball of mud and we lose the option to extract services later.

- **Explicit module interfaces.** A domain exposes a service interface; other domains call *that*, never reach into its internals or its tables.
- **Import-linting in CI.** Mechanically forbid illegal cross-module imports (e.g. `import-linter` contracts). The dependency rule from `BLUEPRINT.md` §4 is enforced by tooling, not goodwill.
- **No shared mutable tables across domains.** Each domain owns its tables. Cross-domain reads go through interfaces or the event log.
- **The event log is the integration backbone.** Domains communicate state changes via execution events, which is *also* how they'd communicate if later split into services — so the extraction path is pre-drawn.

## Consequences

### Positive
- **One image, one `docker compose up`** — the self-host story stays trivial.
- **Refactoring boundaries is cheap** while we're still learning where they belong — move a function, not a network contract.
- **One debugging context, one deploy, one set of logs.** Enormous velocity advantage for a small team.
- **The api/worker split** gives us independent horizontal scaling of the execution tier (run more workers) without distributing the domain logic.

### Negative / accepted costs
- **Discipline is on us.** Without enforced import rules, the modularity rots. We mitigate with CI import-linting from day one — this is not optional.
- **Coarse-grained scaling.** We scale the whole api image or the whole worker image, not an individual domain. Fine at our scale; a non-issue until specific domains have wildly divergent load.
- **A noisy-neighbor blast radius** within a process (one module's bug can affect the process). Mitigated by the api/worker split isolating the two highest-risk runtimes from each other.

### Revisit triggers
Extract a module into its own service when **a specific, measured** condition appears — not on principle:
1. A single domain has a **radically different scaling profile** that coarse image-scaling wastes money on (e.g. node execution needs 50× the api tier).
2. A domain needs an **independent deploy cadence** that the monolith's release train demonstrably blocks.
3. **Team growth** past the point where one codebase's merge/coordination cost exceeds the cost of a network boundary (commonly several independent teams).

Because boundaries are already drawn as enforced modules and integration already flows through the event log, extraction is a mechanical lift — which is the entire point of starting here.
