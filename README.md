<div align="center">

# Axiom

**The orchestration layer for GTM engineers.**
Bring your own keys. Bring your own compute.

</div>

---

Axiom is an open-source, self-hostable **GTM orchestration engine**. Technical go-to-market teams build enrichment, scraping, outreach, and data-processing workflows as DAGs, running against *their own* provider keys (OpenAI, Anthropic, Apollo, Smartlead, Prospeo, custom APIs) on *their own* compute — never metered per action.

It is **not** a CRM, a lead-gen tool, an AI SDR, or an outreach platform. It is the execution engine behind modern GTM workflows. The product is the **engine + the node ecosystem + the builder**, in that order.

> **Status: foundation / pre-code.** This repository currently contains the architecture and planning documents for building Axiom over ~12 months. There is no application code yet — the build begins with Phase 0 (`PHASE_0.md`). These docs are the committed, reviewed source of truth; if you disagree with a decision, open an ADR that supersedes it rather than diverging silently.

---

## Why Axiom

- **Stop being metered.** Action-based pricing punishes volume. Pay your providers directly; Axiom never charges per action — only for hosting, on the optional cloud.
- **Own your stack.** Self-host the engine, the runtime, and the builder. The core is Apache-2.0.
- **Extend it without us.** Most integrations are a declarative manifest, not code — so a non-engineer can add a provider in minutes. Power users drop to code nodes or attach MCP servers.

The wedge is the **Clay-cost refugee** and the **growth agency**; the moat is the **node/template ecosystem + data gravity**, not BYOK or MCP (both are accelerants, copyable in a weekend). See [`BLUEPRINT.md`](BLUEPRINT.md) §1 and [ADR-0005](docs/adr/0005-agencies-as-initial-wedge.md) / [ADR-0006](docs/adr/0006-ecosystem-as-moat.md).

---

## The 60-second architecture

A **modular monolith** (one image, `api` + `worker` modes) on **PostgreSQL + Redis** — *not* microservices, *not* Temporal, *not* Celery. The workflow engine is a custom, Postgres-backed DAG orchestrator: durable run state, `SKIP LOCKED` work-claiming, lease-based crash recovery, and an append-only event log from which history, the live run viewer, and audit all fall out as projections. Python-first backend (FastAPI); Next.js frontend.

The "why" behind each of those is an ADR — they are the expensive-to-reverse decisions:

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-postgres-redis-over-temporal.md) | PostgreSQL + Redis custom engine over Temporal/Celery |
| [0002](docs/adr/0002-modular-monolith.md) | Modular monolith over microservices |
| [0003](docs/adr/0003-python-first.md) | Python-first backend |
| [0004](docs/adr/0004-http-manifest-nodes.md) | HTTP-manifest as the primary node type |
| [0005](docs/adr/0005-agencies-as-initial-wedge.md) | Agencies + Clay-cost refugees as the initial wedge |
| [0006](docs/adr/0006-ecosystem-as-moat.md) | Ecosystem + data gravity as the moat |

---

## Documentation map

Start with `BLUEPRINT.md`; everything else is depth on one slice of it.

| Document | What it covers |
|---|---|
| **[BLUEPRINT.md](BLUEPRINT.md)** | Vision, system architecture, domain boundaries, open-core split, the 12-month roadmap. The keystone. |
| **[docs/adr/](docs/adr/)** | Architecture Decision Records — the choices that are expensive to reverse, each with revisit triggers. |
| **[DOMAIN_MODEL.md](DOMAIN_MODEL.md)** | Entities, relationships, and the execution / credential / node / marketplace models. |
| **[SECURITY.md](SECURITY.md)** | Threat model, the credential vault, scoping, tenant isolation, node trust tiers, SSRF, MCP security. |
| **[SDK_SPEC.md](SDK_SPEC.md)** | The node SDK: three node kinds, the manifest format, the 2-method contract, versioning, publishing. |
| **[UI_SYSTEM.md](UI_SYSTEM.md)** | Information architecture, navigation, the workflow builder UX, interaction principles. |
| **[DESIGN.md](DESIGN.md)** | The locked visual design-token system (Vercel-derived). UI_SYSTEM defers to this for all pixels. |
| **[PHASE_0.md](PHASE_0.md)** | The 4-week de-risking spike plan that opens the build. |
| **[PHASE_1.md](PHASE_1.md)** | The core-engine build plan (weeks 5–12), ending in a dogfooded cold-outbound workflow. |

### Reading orders by role

- **New engineer:** `BLUEPRINT.md` → the ADR index → `DOMAIN_MODEL.md` → `SECURITY.md` → the current phase plan.
- **Node contributor:** `SDK_SPEC.md` → [ADR-0004](docs/adr/0004-http-manifest-nodes.md) → the node sections of `DOMAIN_MODEL.md`.
- **Frontend / design:** `UI_SYSTEM.md` → `DESIGN.md` → `BLUEPRINT.md` §2 (product philosophy).
- **Founder / GTM:** `BLUEPRINT.md` §1 → [ADR-0005](docs/adr/0005-agencies-as-initial-wedge.md) → [ADR-0006](docs/adr/0006-ecosystem-as-moat.md).
- **Security review:** `SECURITY.md` → the credential model in `DOMAIN_MODEL.md` §5 → trust tiers in `SDK_SPEC.md`.

---

## Roadmap at a glance

Phases overlap deliberately, and **every phase ends in something a real user can touch**. Full detail in [`BLUEPRINT.md`](BLUEPRINT.md) §8.

```
Phase 0  Architecture & spikes      — de-risk the irreversible decisions          (wk 1–4)
Phase 1  Core engine                — orchestrator + vault + ~7 nodes + CLI         (wk 5–12)   ← dogfooding starts
Phase 2  Node runtime & SDK         — the public SDK; the contributor flywheel      (wk 10–18)
Phase 3  Workflow builder           — the canvas, the output table, live runs        (wk 16–28)  ← public OSS launch
Phase 4  MCP runtime                — attach MCP servers as nodes                     (wk 26–34)
Phase 5  Team, marketplace, ecosystem — multi-org, marketplace, enrichment cache     (wk 32–42)
Phase 6  Cloud platform             — managed hosting, billing, sandboxed nodes       (wk 40–52)
```

---

## Contributing

Axiom is open-source first; the contributor experience is the moat ([ADR-0006](docs/adr/0006-ecosystem-as-moat.md)). Contribution mechanics (the public node SDK, the registry, templates) land in **Phase 2** — until then, the highest-value contribution is **pressure-testing these documents**. If a decision looks wrong, the right move is a superseding ADR with the trade-offs spelled out, not a quiet divergence.

The core engine, runtime, SDK, and builder are intended to be **Apache-2.0**. Cloud-specific control-plane components may later carry a time-delayed BSL; the engine and SDK stay permanently permissive. See [`BLUEPRINT.md`](BLUEPRINT.md) §5.

---

<div align="center">
<sub>Axiom — open-source revenue infrastructure.</sub>
</div>
