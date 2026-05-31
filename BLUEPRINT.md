# Axiom — Technical Blueprint

> The orchestration layer for GTM engineers.
> **Bring your own keys. Bring your own compute.**

**Status:** Committed (post-review)
**Owner:** Founding CTO
**Last updated:** 2026-05-31
**Audience:** Engineering, design, and founding team building Axiom over the next 12 months.

This is the canonical reference for *what we are building and why*. It is deliberately opinionated. Where it overrides the original product brief, it says so. Decisions that are expensive to reverse are recorded as ADRs (`/docs/adr/`) and linked inline. If you disagree with something here, open an ADR superseding it — do not silently diverge in code.

---

## 1. Product vision

### 1.1 What Axiom is

Axiom is an **open-source, self-hostable GTM orchestration engine**. Technical go-to-market teams use it to build enrichment, scraping, outreach, and data-processing workflows as directed acyclic graphs (DAGs), running against *their own* provider API keys (OpenAI, Anthropic, Apollo, Smartlead, Prospeo, LinkedIn, custom APIs) on *their own* compute.

The product is the **execution engine + the node ecosystem + the builder** — in that order of importance.

### 1.2 What Axiom is not

- Not a CRM.
- Not a lead-generation tool.
- Not an AI SDR.
- Not an outreach platform.
- Not a "better canvas." The canvas is a means; the engine and the nodes are the product.

We position as **open-source revenue infrastructure** — the orchestration engine behind modern GTM workflows.

### 1.3 The wedge (how we enter the market)

We do **not** lead with the full "platform for GTM engineers" vision. We lead with two concrete, self-selecting beachheads — see [ADR-0005](docs/adr/0005-agencies-as-initial-wedge.md):

1. **The Clay-cost refugee.** Clay's action-based pricing becomes punitive at volume. "Bring your own keys, pay your provider directly, we never meter you" is a *quantifiable* value proposition — we can put a savings calculator on the landing page. This wedge self-selects for technical users who can run their own keys.
2. **The growth agency.** Agencies are technical, cost-sensitive, run high volume, and *resell*. An agency that builds its client delivery on Axiom becomes simultaneously a power user, a contributor, and a distribution channel. Agencies — not individual GTM engineers — are our true initial wedge.

### 1.4 The moat (why this compounds)

BYOK and MCP support are **adoption accelerants, not moats** — both are copyable in a weekend. Open source is *distribution*, not defensibility. The real, compounding moat is the flywheel — see [ADR-0006](docs/adr/0006-ecosystem-as-moat.md):

```
   more users ──▶ more node + template contributions ──▶ Axiom does more
       ▲                                                        │
       └────────────────── more reasons to adopt ◀──────────────┘
```

Three reinforcing assets, none of which exist on day one, all of which compound:

- **(a) Ecosystem depth** — a curated, deep node/template library that is genuinely hard to replicate.
- **(b) Data gravity** — execution history + the enrichment cache grow with usage and raise switching cost over time. (This deliberately re-introduces stickiness that pure BYOK removes — see §1.5.)
- **(c) Standard-setting** — the Axiom node SDK becomes *the* way technical GTM people build integrations.

### 1.5 The retention problem we must engineer around

A BYOK tool has **low switching cost by design** — that is both the pitch and the retention risk in one sentence. We deliberately engineer stickiness back in:

- **Execution history + cost analytics** that accrue value the longer a customer runs.
- **The enrichment cache** — never re-pay Apollo for a domain enriched last week. This is simultaneously a cost-saver (wedge) and a lock-in (moat).
- **An ecosystem** of nodes and templates that only run in Axiom.

### 1.6 Why Axiom could fail (and the mitigations we are committing to)

| Failure mode | Likelihood | Mitigation we are committing to |
|---|---|---|
| Beautiful canvas, thin nodes → does less than Clay → users churn | **Highest** | Treat node depth + SDK ergonomics as P0 (Phase 2). Ship ~15 real first-party nodes before the public launch. |
| Scope kills the team before PMF | High | Cut MVP roughly in half vs. the original brief. RAG removed from year 1. Phases overlap; every phase ends in something usable. |
| Contributor flywheel never spins | High | HTTP-manifest node path (no code required) + a great SDK. Test the SDK with a *real* external dev in Phase 2. |
| Data-provider / LinkedIn ToS or legal action | Medium | Don't ship first-party scrapers that invite litigation; push raw scraping to community/BYOC; keep "GTM" positioning provider-neutral. |
| Incumbent ships "BYOK mode" and erases our differentiator | Medium | The moat is the ecosystem + data gravity, not BYOK. Win the flywheel before they copy the feature. |
| BYOK cold-start onboarding wall | Medium | Ship keyless/free nodes (HTTP, public-page fetch, trial AI key) so the first workflow runs in < 5 minutes with zero keys configured. |

---

## 2. Product philosophy

These are the tie-breakers. When two designs are otherwise equal, the earlier principle wins.

1. **The engine is the product; the canvas is a window into it.** Spend the canvas budget on *legibility of execution* (what ran, what it cost, what it returned) over pan/zoom polish.
2. **The data is the hero.** GTM users live in rows. A fast, dense, spreadsheet-like output table matters more than the graph. This is what Clay gets right and generic automation tools get wrong.
3. **Lower the contribution barrier above almost everything else.** The number of community nodes is roughly a function of how hard it is to write one. Most integrations should require a *manifest*, not code — see [ADR-0004](docs/adr/0004-http-manifest-nodes.md).
4. **Reliability over features.** A workflow that silently double-sends an email is worse than a missing feature. Idempotency, durable state, and honest failure reporting are non-negotiable.
5. **Self-host must stay trivial.** Every dependency we add is a dependency a self-hoster must operate. This single principle is why we reject Temporal — see [ADR-0001](docs/adr/0001-postgres-redis-over-temporal.md).
6. **Secrets are an architecture, not a setting.** "Never expose user credentials" is honored by a vault boundary, per-node credential scoping, and last-moment in-memory unwrapping — never by a checkbox. See `SECURITY.md`.
7. **Maintainability > speed. Extensibility > shortcuts. Platform thinking > feature thinking.** When uncertain, choose the option that a contributor six months from now can understand and extend.

---

## 3. System architecture

### 3.1 Deployment shape — modular monolith + worker

We ship a **modular monolith**: one codebase, one deployable image, run in two modes (`api` and `worker`). We do **not** start with microservices — see [ADR-0002](docs/adr/0002-modular-monolith.md). The module boundaries below are *logical* (enforced by import rules and domain interfaces), so that we can later extract a module into its own service at a seam we have already drawn — without paying the distributed-systems tax before we have the scale to justify it.

```
                          ┌────────────────────────────────────────────┐
                          │                Web (Next.js)                  │
                          │   builder canvas · run viewer · marketplace   │
                          └──────────────────┬────────────────────────────┘
                                             │ HTTPS  +  WebSocket (live run state)
                          ┌──────────────────▼────────────────────────────┐
                          │              API process (FastAPI)              │
                          │  ┌─────────┬──────────┬───────────┬─────────┐  │
                          │  │ Identity│ Workflow │ Credential│  Node   │  │
                          │  │ &Tenancy│ Authoring│   Vault   │ Registry│  │
                          │  │  (RBAC) │ +validate│(KMS-wrap) │         │  │
                          │  └─────────┴────┬─────┴───────────┴─────────┘  │
                          │       enqueue run│        read schemas          │
                          └──────────────────┼───────────────────────────────┘
            ┌───────────────┬────────────────┼────────────────┬───────────────┐
            ▼               ▼                ▼                ▼               ▼
      ┌──────────┐   ┌────────────┐   ┌──────────┐    ┌──────────┐   ┌──────────────┐
      │ Postgres │   │   Redis    │   │  Object  │    │   KMS    │   │  pgvector    │
      │ source   │   │ queue +    │   │  store   │    │  (envel. │   │  (deferred — │
      │ of truth │   │ pub/sub +  │   │ (large   │    │   encrypt│   │   not year 1)│
      │ + events │   │ rate-limit │   │ payloads)│    │   keys)  │   │              │
      └────▲─────┘   └─────▲──────┘   └────▲─────┘    └────▲─────┘   └──────────────┘
           │ durable        │ claim         │ payload        │ unwrap
           │ run state      │ ready nodes   │ refs           │ on exec
      ┌────┴────────────────┴───────────────┴────────────────┴─────────────────────┐
      │                          Worker process(es)  (Python)                        │
      │   ┌──────────────────────┐      ┌────────────────────────────────────────┐  │
      │   │   Orchestrator        │      │   Node Runtime                          │  │
      │   │   DAG scheduler:      │─────▶│   load node@version · validate()        │  │
      │   │   ready-set · retries │      │   · execute() · map I/O · cost ledger   │  │
      │   │   · compensation      │      │   ┌──────────────────────────────────┐  │  │
      │   │   · crash recovery    │      │   │ MCP Runtime (MCP client to user-  │  │  │
      │   └──────────────────────┘      │   │ attached MCP servers; node kind)  │  │  │
      │                                  │   └──────────────────────────────────┘  │  │
      │                                  └────────────────────────────────────────┘  │
      └──────────────────────────────────────┬─────────────────────────────────────┘
                                              │ outbound (BYOK — user's own keys)
                       ┌──────────────────────┼────────────────────────┐
                       ▼                      ▼                        ▼
                 OpenAI / Anthropic     Apollo / Prospeo       Smartlead / LinkedIn / MCP servers
```

The diagram above is a *logical* view — the boxes are components, not deployables. Concretely, the repository is **one installable Python package** with multiple run modes, not a package-per-service monorepo:

```
src/axiom/
  shared/        base types + errors (depends on nothing)
  platform/      config · logging (+ redaction) · db · redis
  domains/       the 8 bounded contexts below — internal subpackages, NOT separate dists
                   identity · credentials · workflow_authoring · execution
                   · node_runtime · mcp_runtime · observability · marketplace
  sdk/           the public node contract — kept import-independent so it can be
                   extracted as `pip install axiom-sdk` in Phase 2
  api/           FastAPI run mode      ─┐
  worker/        orchestrator run mode  ├─ thin entrypoints; all import the same domains
  cli/           the axiom CLI         ─┘
migrations/      Alembic (targets axiom.platform.db.Base.metadata)
web/             Phase 3 frontend (empty placeholder until then)
```

Three deliberate consequences of "modular monolith, don't distribute early," each enforced by `import-linter` contracts in `pyproject.toml`:

- **`api` / `worker` / `cli` are run modes of one image, not three services** (the Docker entrypoint dispatches on a `MODE` env var). This supersedes the brief's `apps/{web,api,worker}` framing.
- **Domains are internal subpackages, not independently published packages.** The brief's `packages/*` layout would be premature distribution (ADR-0002). The one component built for future standalone extraction is the node SDK.
- **The manifest format has one logical representation:** authored as YAML/JSON, normalized to JSON on publish, stored as `jsonb` (reconciles `SDK_SPEC.md` ↔ `DOMAIN_MODEL.md`).

### 3.2 The execution engine — custom, Postgres-backed

The orchestrator is a **custom DAG engine on Postgres + Redis**, not Temporal and not Celery — see [ADR-0001](docs/adr/0001-postgres-redis-over-temporal.md). Rationale in brief:

- Our workflows are short-to-medium-lived, DAG-shaped, fan-out/fan-in over rows. This is *not* the multi-day, code-defined, human-in-the-loop saga problem Temporal solves.
- Temporal requires operating a separate Go cluster + its own persistence store, which **torpedoes the "self-host in 10 minutes" promise** that drives OSS adoption.
- Celery is a task queue, not a workflow engine — no first-class DAG, no durable execution, no compensation. We'd rebuild the engine on top of it anyway.

Core mechanics:

- **Durable state in Postgres.** Run state and every per-node state row (`status`, `attempt`, `input_ref`, `output_ref`, `lease_until`) live in Postgres. Postgres *is* the source of truth.
- **Ready-set scheduling.** A node is `READY` when all upstream edges are satisfied (succeeded, or conditionally satisfied). Workers claim ready nodes via `SELECT ... FOR UPDATE SKIP LOCKED` — Postgres gives us a correct, concurrent work queue for free.
- **Redis is transport, not truth** — pub/sub for live UI updates and per-provider rate-limit token buckets. If Redis is wiped, no run state is lost.
- **Crash recovery via leases.** Each claimed node holds a `lease_until`. A reaper requeues nodes whose lease expired. Because state is already durable, a killed worker resumes a run exactly where it stopped.
- **Idempotency by construction.** Every execution is keyed by `(run_id, node_id, attempt)` so retries never double-send.
- **Time-delays as scheduled re-entry.** Long waits ("wait 3 days, then check for a reply") are persisted as "resume at T" rows and re-enqueued by the scheduler — *not* held in memory. This lets drip-sequence workflows run on the custom engine without Temporal.

### 3.3 Execution as an event log

Execution is **event-sourced** (the execution layer *only* — not the whole app; full event sourcing everywhere is an over-engineering trap). Every state change in a run is an immutable, append-only event; current state is a projection.

This one decision is why three "features" fall out for free:

- **Execution history** → replay the event log.
- **Live run viewer** → subscribe to the run's Redis channel; the UI is just another projection.
- **Audit trail** → the event log *is* the audit log.

```
Trigger (manual | schedule | webhook | event)
      │
      ▼
RunRequested ──▶ [API validates definition + tenancy + required credentials present]
      │
      ▼
RunStarted ─────▶ orchestrator computes ready-set from the pinned workflow version
      │
      ▼  (per node, repeated)
NodeReady ─▶ NodeStarted ─▶ [NodeRetried]* ─▶ NodeSucceeded | NodeFailed
      │                                              │
      │                                              ▼
      │                                    NodeCompensating ─▶ NodeCompensated   (best-effort)
      ▼
RunSucceeded | RunFailed | RunCancelled
      │
      ├──▶ Redis pub/sub ──▶ WebSocket ──▶ live UI
      └──▶ Postgres append ──▶ projection ──▶ run viewer + cost ledger + audit log
```

Every event carries `org_id`, `run_id`, `node_id`, `attempt`, `timestamp`, and a payload **reference**. Large payloads (a 10k-row enrichment result) live in object storage; the event holds the reference. Postgres stays lean and replay stays cheap.

### 3.4 Data flow — a single node execution

```
orchestrator claims a NodeReady (FOR UPDATE SKIP LOCKED)
   → load node package @ pinned version (immutable for this workflow version)
   → resolve inputs: map upstream outputs via edge mappings (expression / JSONPath)
   → fetch + unwrap ONLY the credentials the node's manifest declares (in-memory; never logged)
   → node.validate(inputs)        ──fail──▶ NodeFailed   (no external call made)
   → ctx.cache lookup (enrichment cache)  ──hit──▶ return cached output (no spend)
   → node.execute(ctx, inputs)
        ├─ small output → inline in event payload
        └─ large output → object store; store ref in event
   → record cost (tokens / credits / $), duration, status
   → emit NodeSucceeded(output_ref) → recompute ready-set → unblock downstream
```

The critical invariant: **outputs flow through the event log, never through shared mutable state.** This is what makes parallel branches, replay, and crash recovery correct rather than hopeful.

---

## 4. Domain boundaries

Each boundary exists because the thing inside it has a *different rate of change, different security lifecycle, or different consistency need* than its neighbors. Full entity/relationship detail is in `DOMAIN_MODEL.md`.

| Domain | Owns | Why it is a boundary |
|---|---|---|
| **Identity & Tenancy** | orgs, users, memberships, RBAC, sessions | Every other domain is scoped by `org_id`. Multi-tenant isolation must be enforced in *one* place or it leaks everywhere. Highest-stakes boundary. |
| **Credential Vault** | encrypted provider keys, MCP auth secrets | Secrets have a wholly different lifecycle (encryption, rotation, never-log) than business data. Isolate so a bug's blast radius is contained and auditable. |
| **Workflow Authoring** | workflow definitions, versions, validation | The *design-time* concept: pure data + a validator, no execution concerns. Versioning lives here so a running execution pins the exact definition it started with. |
| **Execution / Orchestration** | runs, node states, scheduling, retries, compensation, the event log | The *run-time* concept — the actual product. Strictly separated from authoring so the engine can change without touching the editor. |
| **Node Runtime** | node interface, loading, I/O contracts, trust tiers | The plugin boundary; third-party code crosses it. Must be the most rigorously contracted seam in the system. See `SDK_SPEC.md`. |
| **MCP Runtime** | MCP registration, discovery, client, permissions, health | A *specialization* of Node Runtime — an MCP server is "a node whose implementation is a remote tool call." Built as a node kind, not a parallel universe. |
| **Observability & Cost** | logs, metrics, traces, per-run cost ledger | A cross-cutting **read model** built from the execution event stream, never on the live execution path, so analytics load can never threaten execution. |
| **Marketplace** | node/template listings, install counts, attribution | The flywheel surface. Read-mostly, abuse-prone, community-facing — a different operational profile from the execution core. |

**Dependency rule:** domains depend *inward* toward Identity/Tenancy and the event log; nothing depends on the UI; the Execution domain depends on Node Runtime through an interface, never the reverse. Import-linting enforces this.

---

## 5. Open-core boundary

What is open source vs. cloud-only — see [ADR-0006](docs/adr/0006-ecosystem-as-moat.md) and §9 of the design review.

| Open source (Apache-2.0, self-hostable) | Cloud-only (commercial) |
|---|---|
| Workflow engine / orchestrator | Hosted, managed, autoscaled execution |
| Node runtime + node SDK | Sandboxed execution of *unverified* community code nodes |
| MCP runtime | SSO/SAML, SCIM, advanced/granular RBAC |
| Core nodes (HTTP, AI providers, common GTM) | Audit-log export, compliance posture (SOC 2) |
| Workflow builder UI | Cross-org cost analytics + alerting |
| Self-host docs, Docker Compose | Managed secrets (KMS), key rotation as a service |
| CLI + SDK | Hosted MCP directory + curated marketplace |

**Licensing:** core under **Apache-2.0** to maximize adoption and contribution. *Not* AGPL/SSPL out of the gate — it scares off exactly the technical/agency users we want and complicates contributions. If we later need to stop a hyperscaler reselling our cloud, we may apply **BSL** to *cloud-specific control-plane components only*, time-delayed to Apache. The engine and SDK stay permanently permissive — that is where contributions come from.

---

## 6. Technology choices (committed)

| Layer | Choice | Notes |
|---|---|---|
| Language (backend) | **Python 3.12+** | See [ADR-0003](docs/adr/0003-python-first.md). Aligns engine, AI/data nodes, and the team. |
| API framework | **FastAPI** | Async, Pydantic v2, OpenAPI for free. |
| ORM / migrations | **SQLAlchemy 2.0 (async) + Alembic** | |
| Datastore (truth) | **PostgreSQL 16+** | Run state, event log, all domain data. |
| Cache / transport | **Redis 7+** | Pub/sub, rate-limit buckets, ephemeral coordination. Never the source of truth. |
| Object store | **S3-compatible** (MinIO for self-host) | Large run payloads. |
| Secrets | **Envelope encryption via KMS** | Pluggable backend (AWS/GCP KMS, Vault Transit, file-based for self-host). See `SECURITY.md`. |
| Frontend | **Next.js + TypeScript + Tailwind + shadcn/ui + React Flow** | Design system already locked in `DESIGN.md`. |
| Node primary path | **HTTP-manifest (declarative)** | See [ADR-0004](docs/adr/0004-http-manifest-nodes.md). |
| Node power path | **Python code nodes** | One code language in year 1. JS/TS code-node runtime deferred. |
| RAG / vector | **Deferred — out of scope for year 1** | Contradicts "engine is the product." Cut entirely. |

---

## 7. Performance & scale posture

The brief's target — **10,000 executions/day "without architecture changes"** — is honestly modest: ~0.12 runs/sec average, bursting to maybe ~50/sec. The Postgres+Redis design clears this with large headroom.

- **Real ceiling:** Postgres write throughput on the event log, reached at *millions* of node executions/day. Mitigation (deferred): partition `execution_events` by month and/or by `org_id`; archive cold partitions to object storage.
- **Orchestration overhead target:** < 100 ms per node hand-off (claim → load → resolve inputs), exclusive of the external provider call.
- **We will have raised a Series A before we hit the wall.** We do not pay Temporal's operational tax now to solve a problem we'll have the team to solve later. The orchestrator lives behind an interface, so a *specific class* of long-running workflow could be delegated to Temporal in the future if ever genuinely required.

---

## 8. Long-term roadmap

Detailed Phase 0 plan in `PHASE_0.md`. Phases **overlap deliberately** — a strict waterfall wastes a small team — and **every phase ends in something a real user can touch.** We **dogfood from Phase 1**: we run our own outbound on Axiom; if it's too painful for us, it's not ready for users.

| Phase | Window | Goal | Ends when… |
|---|---|---|---|
| **0 — Architecture & spikes** | Wk 1–4 | De-risk the irreversible decisions; throwaway spikes for the orchestrator and one end-to-end node. | A 3-node DAG runs, survives a worker kill mid-run, and resumes. ADRs locked. |
| **1 — Core engine** | Wk 5–12 | Production orchestrator (DAG, parallel, retries, scheduling, webhooks, event log, crash recovery) + auth + single-org tenancy + vault + 5–8 real nodes + CLI. | A real cold-outbound workflow runs end-to-end from the CLI against live keys, with retries, cost tracking, queryable history. **Dogfooding starts.** |
| **2 — Node runtime & SDK** | Wk 10–18 | Formal node interface, HTTP-manifest format, code-node path, versioning/pinning, local registry, **public SDK + docs**. | An external dev adds an integration via manifest in < 30 min unaided. 15+ first-party nodes. |
| **3 — Workflow builder** | Wk 16–28 | Next.js canvas, node library, JSONSchema-driven config forms, the **output table**, live run viewer, command palette, `DESIGN.md` applied. | A user builds + runs a workflow entirely in-UI and reads results in a dense table. **Public OSS launch / first design partners.** |
| **4 — MCP runtime** | Wk 26–34 | MCP registration, discovery→auto-node, auth, permissions, health; 2–3 reference GTM MCP servers. | A user attaches an external MCP server and uses its tools as nodes, with admin-controlled permissions. |
| **5 — Team, marketplace & ecosystem** | Wk 32–42 | Multi-org/team RBAC (the "invoke not read" credential rule), public node + template marketplace, enrichment cache, agency multi-client structure. | External contributors publish nodes/templates others install. First agency runs client work on Axiom. |
| **6 — Cloud platform** | Wk 40–52 | Managed hosting, Stripe seat/hosting billing (never per-action), sandboxed unverified-node execution, SSO/audit export, managed KMS, autoscaling. | A paying customer runs production workflows on Axiom Cloud with isolated, secure execution. |

**Explicitly deferred past the 12-month window:** RAG infrastructure (a whole separate product), JS/TS code-node runtime, Gemini/Ollama/OpenRouter breadth beyond the first two AI providers, advanced AI features. The orchestration engine is the product.

---

## 9. Decisions overriding the original brief

For traceability — the brief said one thing, we committed to another. Each links to its ADR where the reversal is expensive.

| Brief said | Committed decision | Why |
|---|---|---|
| Celery OR Temporal | Custom Postgres+Redis engine | [ADR-0001](docs/adr/0001-postgres-redis-over-temporal.md) — Temporal breaks self-host; Celery isn't a workflow engine; our load needs neither. |
| Microservices (api/worker/web as services) | Modular monolith + worker process | [ADR-0002](docs/adr/0002-modular-monolith.md) — premature distribution kills small teams. |
| Node language unspecified | HTTP-manifest (80%) + Python code (20%) + MCP | [ADR-0004](docs/adr/0004-http-manifest-nodes.md) — node ergonomics decide whether the flywheel spins. |
| Every node sandboxed | Sandbox only cloud-run *unverified* code | `SECURITY.md` — BYOC moves the trust boundary to the user; sandboxing everything is a year of wasted work. |
| 5-method node interface (incl. `retry()`, `metadata()`) | 2 behavioral methods + a declarative manifest | `SDK_SPEC.md` — retry is engine policy; fewer required methods = lower contribution barrier. |
| MCP / BYOK is the moat | Ecosystem + data gravity is the moat; MCP/BYOK are accelerants | [ADR-0006](docs/adr/0006-ecosystem-as-moat.md) — open protocols and BYOK are copyable in a weekend. |
| 8-item MVP incl. teams + marketplace + MCP | Engine + SDK + builder + ~15 nodes first | §8 — ship something usable in months, not a dark build for a year. |
| Rollbacks as a core feature | Best-effort per-node compensation, deprioritized | §3.2 — most GTM side effects (a sent email) are not compensable. |
| RAG infrastructure in scope | Cut entirely from year 1 | §8 — a separate product; contradicts "engine is the product." |

---

## 10. Document map

- `README.md` — repo front door; orientation + reading orders by role.
- `BLUEPRINT.md` — this file. Vision, architecture, boundaries, roadmap.
- `docs/adr/` — Architecture Decision Records (the expensive-to-reverse choices).
- `docs/DEVELOPMENT.md` — local-development workflow (setup, the gate, the stack, test lanes).
- `PHASE_0.md` — the de-risking spike plan for weeks 1–4.
- `PHASE_1.md` — the core-engine build plan for weeks 5–12.
- `DOMAIN_MODEL.md` — entities, relationships, execution/credential/node/marketplace models.
- `SECURITY.md` — threat model, vault, scoping, tenant isolation, trust tiers, SSRF, MCP security.
- `SDK_SPEC.md` — node SDK, manifest format, lifecycle, versioning, publishing.
- `UI_SYSTEM.md` — design language, IA, navigation, builder UX.
- `DESIGN.md` — the locked visual design-token system (Vercel-derived).
- `CONTRIBUTING.md` · `LICENSE` · `NOTICE` · `DCO` — Apache-2.0 + DCO contribution terms (ADR-0007).
