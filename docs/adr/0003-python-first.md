# ADR-0003: Python-first backend

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founding CTO, Principal Architect
- **Related:** `BLUEPRINT.md` §6, `SDK_SPEC.md`, ADR-0004

## Context

We must commit to one backend language for the engine, the API, the node runtime, and the year-1 code-node path. This is one of the highest-leverage decisions in the project because it interacts directly with the contributor flywheel: **the language nodes are written in helps determine how many community nodes get written.**

The forces in tension:

- **The integration-contributor community lives in TypeScript/JavaScript.** n8n (TS) has ~1,200+ community nodes; Make, Zapier, Pipedream are all JS. The "I wrote a quick Smartlead integration" crowd skews heavily JS.
- **The AI / data community lives in Python.** Every major LLM SDK, embedding library, data-wrangling tool, and the MCP Python SDK are Python-first. GTM workflows are increasingly AI-shaped.
- **A split-language host is the worst of both worlds.** A Python engine running a JS node sandbox (or vice versa) means operating a cross-language execution boundary — serialization, two runtimes, a sandbox to maintain — from day one. That is exactly the complexity a small team should defer.
- **Team fit.** The founding team's depth determines execution speed in the critical first year.

## Decision

The backend — engine, API, node runtime, and the **year-1 code-node path** — is **Python 3.12+** (async-first, FastAPI, SQLAlchemy 2.0 async, Pydantic v2). The frontend is TypeScript (Next.js); that is a separate, uncontested choice.

Crucially, we **decouple "what language nodes are written in" from this decision** via [ADR-0004](0004-http-manifest-nodes.md): the *primary* node type is a **declarative HTTP manifest with no code at all**, which is language-agnostic and contributable by non-engineers. Python is only the *power-user* code path, not the only contribution path. A JS/TS code-node runtime is deferred, not foreclosed.

## Alternatives considered

### TypeScript / Node for the whole backend — seriously considered, rejected for *this* team and product
Strong case: aligns with the JS integration-contributor pool and unifies frontend/backend language. Rejected because:
- The AI/data ecosystem we'll lean on heavily (LLM SDKs, MCP Python SDK, embeddings, data tooling) is materially richer and more mature in Python.
- The contributor-pool advantage of TS is **neutralized by ADR-0004**: since the dominant contribution path is a *no-code manifest*, we capture non-engineer and cross-language contributors regardless of the engine's language.
- **Caveat recorded honestly:** if the founding team were TS-dominant, this ADR should flip — and the engine should then be TS too, never a Python-host/JS-node split. The default assumption here is a Python-capable founding team. *If that assumption is wrong, this ADR must be reopened before Phase 0 ends.*

### Go for the engine — rejected
Excellent for a high-throughput orchestrator, but: the team-velocity hit of writing product surface in Go pre-PMF is real; the AI/data ecosystem is weaker; and code nodes in Go are a hostile authoring experience for our audience. Our scale target (ADR-0001) does not need Go's performance ceiling.

### Polyglot from day one (Python engine + JS node sandbox) — rejected for year 1
This is the eventual destination (a JS code-node runtime in a later phase), not the starting point. Building and operating a JS sandbox *while* building the engine is the cross-language tax we explicitly defer.

## Consequences

### Positive
- **One language across engine, API, runtime, and code nodes** — minimal context-switching for a small team, simplest possible runtime in year 1.
- **First-class AI/data ecosystem** for the workflows our users actually build, and for the MCP runtime (Python MCP SDK).
- **FastAPI + Pydantic v2** give us async throughput, typed I/O, and OpenAPI generation with low ceremony.
- **The flywheel is protected** despite Python not being the JS community's home turf, because no-code manifests are the main contribution path.

### Negative / accepted costs
- **Python code nodes won't attract the JS integration crowd** to the *code* path. Accepted, because (a) manifests cover ~80% of integrations and (b) a JS/TS code-node runtime is on the long-term roadmap.
- **Raw Python performance** is below Go/Rust. Mitigated by async I/O (the workload is I/O-bound on provider calls, not CPU-bound) and by ADR-0001's modest scale target.
- **GIL considerations** for CPU-bound node work. Mitigated by multi-process workers and offloading CPU-heavy nodes; revisited only if profiling shows it matters.

### Revisit triggers
1. The founding team turns out to be **TS-dominant** — reopen *before* Phase 0 ends; if flipped, the engine goes TS too (never split-host).
2. A JS/TS **code-node runtime** is promoted into scope — that is an *addition* under a new ADR, not a reversal of this one.
3. Profiling shows the orchestrator is **CPU-bound in Python** in a way async + multiprocess can't address at our real load.
