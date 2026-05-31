# ADR-0001: PostgreSQL + Redis custom engine over Temporal/Celery

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founding CTO, Principal Architect
- **Related:** `BLUEPRINT.md` §3.2, `PHASE_0.md` (Spike A)

## Context

The workflow engine is the core product. We must execute DAGs with parallel branches, retries, scheduling, event triggers, crash recovery, and best-effort compensation. The original brief said "Celery OR Temporal." We must also honor a load-bearing product pillar: **self-hosting must stay trivial** — the OSS adoption motion depends on "clone, `docker compose up`, running in 10 minutes."

The forces:

- **Workload shape.** Axiom workflows are short-to-medium-lived (seconds to a few minutes per node), DAG-shaped, fanning out and in over rows of data. The longest-lived case is a drip sequence ("wait 3 days, then check for a reply").
- **Workflows are data, not code.** Users *draw* a DAG in a canvas; it is stored as JSON. The engine interprets that data. Workflows are not hand-written orchestration programs.
- **Team size.** Small (single-digit engineers) pre-PMF. Operational surface area is a direct tax on velocity.
- **Self-host is a product feature, not a deployment detail.** Every infrastructure dependency we require is one a self-hoster must install, operate, and debug — and then open a GitHub issue about.
- **Scale target.** 10,000 executions/day "without architecture changes" (~0.12/sec average, bursting to ~50/sec). Modest.

## Decision

We build a **custom DAG orchestrator on PostgreSQL + Redis.** Postgres is the durable source of truth for run state and the append-only event log. Redis is transport only — pub/sub for live UI and per-provider rate-limit token buckets. We use **neither Temporal nor Celery.**

The orchestrator behind a `WorkflowEngine` interface so a *specific class* of workflow could be delegated elsewhere in the future without rewriting callers.

## Alternatives considered

### Temporal — rejected (for now)
Temporal is genuinely excellent at durable execution and the *correct* enterprise answer. It loses here on two specific points, not on quality:

1. **It breaks the self-host promise.** Running Axiom would mean operating a Temporal **Go cluster plus its own persistence store** (Cassandra/Postgres/MySQL). "Self-host in 10 minutes" dies instantly. This alone is near-disqualifying for an OSS-first product.
2. **Impedance mismatch with data-defined workflows.** Temporal's model is *code-defined* workflows with deterministic replay. Our DAG is *data*. We would be running our own DAG interpreter *inside* a Temporal workflow, where every node call is a non-deterministic activity and our DAG-walking logic must obey Temporal's determinism rules. It is a known-awkward fit for visual/data-driven products and adds a layer of indirection that fights the framework.

### Celery — rejected
Celery is a **task queue, not a workflow engine.** No first-class DAG, no durable execution, no retries-with-state, fragile `chord`/`canvas` primitives for real DAGs, no compensation. We would rebuild the orchestrator *on top of* Celery and fight it the whole way — inheriting its operational quirks while getting none of the engine semantics we need.

### Off-the-shelf workflow libs (Prefect, Dagster, Airflow) — rejected
These are data-engineering / pipeline schedulers optimized for batch ETL and operator authoring, not multi-tenant, user-defined, row-level GTM workflows with per-node credentials and a live builder. Wrong abstraction, heavy footprint, and they too complicate self-host.

## The custom engine, in brief

- **Durable state in Postgres:** run + per-node-state rows (`status`, `attempt`, `input_ref`, `output_ref`, `lease_until`, `error`).
- **Ready-set scheduling:** a node is `READY` when all upstream edges are satisfied. Workers claim ready nodes via `SELECT ... FOR UPDATE SKIP LOCKED` — Postgres provides a correct concurrent work queue with no extra component.
- **Crash recovery via leases:** a reaper requeues nodes whose `lease_until` elapsed; durable state means the run resumes exactly where it stopped.
- **Idempotency by construction:** every execution keyed by `(run_id, node_id, attempt)`; retries never double-send.
- **Event-sourced execution:** state changes are immutable events; history, the live viewer, and the audit log are all projections of one log.
- **Long waits as scheduled re-entry:** "resume at T" is a row the scheduler re-enqueues — not an in-memory timer. This handles drip sequences without Temporal's durable-timer machinery.

This is on the order of a few thousand lines of careful Python — not a distributed-systems research project — *because we are solving the narrow case, not the general one.*

## Consequences

### Positive
- **Self-host stays trivial:** Postgres + Redis are dependencies every target user already understands. `docker compose up` works.
- **One mental model, one set of failure domains.** No second persistence store, no separate cluster to reason about.
- **Free goodies from `SKIP LOCKED` + the event log:** a correct work queue, replay, audit, and the live viewer all fall out of two primitives.
- **Full control** over retry taxonomy, cost accounting, and node lifecycle — no framework constraints to bend around.

### Negative / accepted costs
- **We own the engine's correctness.** Idempotency, lease/reaper races, and the ready-set algorithm are *our* bugs to find. Phase 0 Spike A exists precisely to de-risk this; it must include a chaos test (kill a worker mid-run).
- **No free durable timers / signals / long-running saga tooling.** We re-implement the slivers we need (scheduled re-entry). Acceptable because our longest workflows are drip sequences, not 90-day human-approval sagas.
- **Postgres write throughput is the eventual ceiling** (event log). Reached at millions of node-executions/day — far beyond year-1 needs.

### Revisit triggers
Reopen this ADR if **any** hold:
1. The roadmap commits to genuinely long-running, multi-week, human-in-the-loop sagas with complex signal/timer semantics as a *core* use case.
2. Sustained load approaches **millions of node-executions/day** and event-log partitioning is no longer enough.
3. Engine-correctness bugs (double-execution, lost runs) persist past Phase 1 despite focused effort — a signal we under-estimated the build.

In those cases, the `WorkflowEngine` interface lets us route a *subset* of workflows to Temporal without a full rewrite.
