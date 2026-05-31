# Axiom — Phase 0: Architecture & De-risking

**Status:** Committed (post-review)
**Window:** Weeks 1–4
**Last updated:** 2026-05-31
**Audience:** The founding engineering team executing the first four weeks.

> **Phase 0's only job is to make the expensive-to-reverse decisions cheap to live with.** We do not build product in Phase 0. We build *throwaway spikes* whose purpose is to either confirm a load-bearing assumption or expose it as wrong while it's still free to change our mind. Code written here is expected to be deleted. The deliverable is **confidence + locked ADRs**, not features.

If a spike proves an assumption wrong, that is a *success*, not a failure — it's the cheapest that lesson will ever be.

---

## 1. What Phase 0 is and is not

| Phase 0 IS | Phase 0 is NOT |
|---|---|
| Throwaway spikes that de-risk the irreversible | Building the real engine, API, or UI |
| Locking the ADRs with evidence | Writing production code |
| Measuring whether the custom engine is viable | Optimizing anything |
| Proving one node runs end-to-end through the vault | Building many nodes |
| Confirming team-fit assumptions (Python) | Hiring/scaling decisions |
| Producing ADRs + a Phase 1 plan | A demo for users |

**Exit gate:** every item in §7 is checked, or a spike has surfaced a finding that forces an ADR revision (also a valid exit — we just exit *informed*).

---

## 2. The decisions Phase 0 must lock

The ADRs (`docs/adr/`) are *proposed* until Phase 0 backs them with evidence. Phase 0 converts them to *accepted-with-evidence* or *revised*.

| Decision | ADR | How Phase 0 validates it | Revisit trigger |
|---|---|---|---|
| Custom Postgres+Redis engine over Temporal | [0001](docs/adr/0001-postgres-redis-over-temporal.md) | **Spike A** proves claim/retry/crash-recovery works and is simple enough | Engine correctness can't be made reliable in the spike timebox |
| Modular monolith | [0002](docs/adr/0002-modular-monolith.md) | **Spike C** proves import-linting enforces boundaries + api/worker split runs from one image | Boundaries can't be cleanly enforced |
| Python-first | [0003](docs/adr/0003-python-first.md) | **§4.0 team-fit check** — confirm the founding team is Python-capable *before* committing | Team is TS-dominant → flip ADR-0003 + reconsider engine language |
| HTTP-manifest primary node | [0004](docs/adr/0004-http-manifest-nodes.md) | **Spike B** proves a real provider (Apollo) works as a *manifest*, not code | A real provider can't be expressed declaratively |
| Agencies/Clay-cost wedge | [0005](docs/adr/0005-agencies-as-initial-wedge.md) | **§5 product validation** — 3–5 customer conversations test the cost narrative | The savings story doesn't move technical buyers |
| Ecosystem moat | [0006](docs/adr/0006-ecosystem-as-moat.md) | Strategic; validated over time, not in a spike. Phase 0 only confirms the *enabling* bet (Spike B: manifest authoring is easy) | — |

### 4.0 The team-fit check (do this in Week 1, before anything else)
[ADR-0003](docs/adr/0003-python-first.md) explicitly assumes a **Python-capable founding team.** This is the cheapest assumption to invalidate and the most expensive to discover late. **Week 1, day 1: confirm it.** If the founding team is TS-dominant, stop and reopen ADR-0003 — and if it flips, the engine goes TS too (never a Python-host/JS-node split). Everything downstream depends on this being right, so spend an hour on it now.

---

## 3. The spikes

Three spikes, ranked by risk. **Spike A is the one that can sink the architecture** — start it first and give it the most time. Each spike has a falsifiable hypothesis and explicit **PASS** / **KILL** criteria.

### Spike A — The custom orchestrator (highest risk)

> **Hypothesis:** A correct, crash-recoverable DAG engine on Postgres + Redis is buildable in a few thousand lines and is *simple enough* that a small team can own its correctness — making Temporal unnecessary ([ADR-0001](docs/adr/0001-postgres-redis-over-temporal.md)).

**What we build (throwaway):**
- A minimal `node_state` table and the **ready-set claim loop** using `SELECT … FOR UPDATE SKIP LOCKED`.
- A hardcoded 3-node DAG: `A → {B, C}` (fan-out) — enough to prove parallel claiming.
- Per-node **retry with exponential backoff** driven by a typed error class.
- A **lease + reaper**: claimed nodes hold `lease_until`; a reaper requeues expired ones.
- **Idempotency**: execution keyed by `(run_id, node_id, attempt)`; a deliberately side-effecting "node" (increment a counter / append to a file) to prove no double-fire on retry/recovery.
- Append-only event emission for each transition.

**What we measure:**
- **The chaos test (the whole point):** start the DAG, `kill -9` a worker mid-execution of node B, confirm the run **resumes and completes exactly once** with no double side-effect.
- Orchestration overhead per hand-off (claim→resolve) — sanity-check against the <100 ms target (`BLUEPRINT.md` §7).
- Lines of code + subjective "could a new engineer understand this?" read.
- Concurrent claim correctness: two workers never claim the same node.

**PASS:** the 3-node DAG runs; a mid-run `kill -9` resumes to exactly-once completion; parallel branches claim concurrently without collision; the code is comprehensible.
**KILL (→ reopen ADR-0001):** crash-recovery races prove intractable in the timebox, or the engine balloons past ~a few thousand lines of essential complexity, or exactly-once can't be achieved without heroics. → Evaluate routing execution to Temporal behind the `WorkflowEngine` interface.

### Spike B — One real node, end-to-end, through the vault

> **Hypothesis:** A real GTM provider (Apollo enrich) works as a **declarative HTTP manifest** ([ADR-0004](docs/adr/0004-http-manifest-nodes.md)), and a credential flows from an envelope-encrypted store into the call **without ever being logged or persisted in plaintext** (`SECURITY.md` §5).

**What we build (throwaway):**
- A minimal manifest parser that executes the Apollo `request` block (§3 of `SDK_SPEC.md`), mapping `inputs` → request and `response` → `outputs`.
- A minimal vault: envelope encryption with a **real KMS** (or a KMS-emulator like LocalStack for the spike), unwrap-in-memory at call time, `last4`-only display.
- The instrumented `ctx.http` with the **redaction layer** (`SECURITY.md` §5.4).
- A redaction test: assert the Apollo key value **never** appears in captured logs.

**What we measure:**
- Does the manifest-only approach actually express a real provider call + response mapping, with **zero handwritten code**?
- Does the credential round-trip (encrypt → store → unwrap → inject → call) work, and is plaintext provably absent from logs/DB/events?
- How long did it take to author the manifest *as if a contributor*? (Early read on the 30-minute DX bar.)

**PASS:** Apollo enrich runs from a manifest against a live key; the credential is envelope-encrypted at rest and unwrapped only in-memory; the redaction test passes (key never logged).
**KILL (→ reopen ADR-0004):** a *common* real provider genuinely cannot be expressed declaratively without an escape-hatch that looks like code → re-examine where the manifest/code boundary sits.

### Spike C — Modular-monolith skeleton

> **Hypothesis:** One codebase, one image, two run modes (`api`/`worker`) with **mechanically enforced** module boundaries is workable and keeps self-host trivial ([ADR-0002](docs/adr/0002-modular-monolith.md)).

**What we build (throwaway):**
- A repo skeleton with the domain modules as packages and **import-linter contracts** encoding the dependency rule (`BLUEPRINT.md` §4).
- One image, two entrypoints (`api`, `worker`).
- A `docker compose up` bringing up Postgres + Redis + api + worker.
- A CI job that **fails on an illegal cross-module import** (prove the enforcement bites).

**What we measure:**
- Does `docker compose up` give a running api+worker against Postgres+Redis in one command? (The self-host promise.)
- Does the import-linter actually fail CI when we *intentionally* write a forbidden import?

**PASS:** one command brings the stack up; an intentional illegal import fails CI.
**KILL (→ reopen ADR-0002):** boundaries can't be enforced without excessive ceremony (unlikely; low-risk spike).

---

## 4. Architecture validation plan

Beyond the spikes, Phase 0 confirms the cross-cutting choices on paper + minimal proof:

- **Event-sourced execution model** — validate that history, live-view, and audit can all be projections of the Spike A event stream (sketch the projection queries; don't build the UI).
- **Postgres as queue** — confirm `SKIP LOCKED` throughput is comfortably above our bursty ~50/sec target on commodity hardware (a quick load sketch in Spike A, not a benchmark suite).
- **Redis as transport-only** — confirm that wiping Redis loses *no* run state (kill Redis mid-run in Spike A; the run must still recover from Postgres).
- **Secrets backend pluggability** — confirm the vault interface in Spike B can swap KMS ↔ file-based without touching callers (self-host vs cloud).
- **Trust-tier deployment gating** — confirm on paper that "Cloud runs only verified + manifest nodes until Phase 6" is enforceable as a registry/runtime check (`SECURITY.md` §6).

---

## 5. Product validation (parallel track, non-engineering)

The wedge ([ADR-0005](docs/adr/0005-agencies-as-initial-wedge.md)) is an assumption too, and Phase 0 is the time to test it cheaply, in parallel with the spikes:

- **3–5 conversations** with target users: at least 2 agencies, 2 Clay-cost-refugee teams.
- **Test the savings narrative:** does the "pay your provider directly, never metered" math actually move them? Get a real number from a real Clay/Apollo bill.
- **Probe the onboarding wall:** how do they react to "bring 6 API keys before you see value"? Validate the keyless-first-run mitigation idea.
- **Output:** a one-page findings note. If the cost narrative doesn't land, that's an ADR-0005 revisit trigger *before* we build the wedge-specific features.

This is not a sales motion — it's de-risking the strategy with the same rigor as the engine.

---

## 6. Technical risk register

| ID | Risk | Likelihood | Impact | Mitigation / owner |
|---|---|---|---|---|
| R1 | **Engine crash-recovery has subtle races** (double-execution, lost runs) | Med | **Critical** | Spike A chaos test is the gate; idempotency key is the structural defense. If unsolved in timebox → ADR-0001 revisit. |
| R2 | **Team is TS-dominant**, making Python-first a velocity tax | Low–Med | High | §4.0 Week-1 check *before* committing. Cheap to catch now, expensive later. |
| R3 | **A real provider can't be expressed as a manifest** | Low–Med | High | Spike B against a real API. If common providers need code, the moat thesis weakens — surface immediately. |
| R4 | **Credential plaintext leaks** into logs/DB/events | Med | **Critical** | Spike B redaction test (assert key never logged); vault interface has no plaintext-returning path. |
| R5 | **SSRF via user-supplied URLs** (metadata theft) | Med (cloud) | **Critical** | Designed in `SECURITY.md` §8; Phase 0 only confirms the egress-filter approach on paper (built in Phase 1, hardened by Phase 6). |
| R6 | **`SKIP LOCKED` doesn't scale** to bursty load | Low | Med | Quick load sketch in Spike A; target (~50/sec) is modest and well within Postgres. |
| R7 | **Scope creep** — Phase 0 drifts into building real product | **High** | Med | This doc's IS/IS-NOT table; throwaway code is *expected*; weekly check against the exit gate. |
| R8 | **Modular boundaries rot** without enforcement | Med | Med (long-term) | Spike C proves import-linter fails CI on violation; enforcement from day one of Phase 1. |
| R9 | **Wedge narrative doesn't convert** | Med | High (strategic) | §5 customer conversations; revisit ADR-0005 before building wedge features. |
| R10 | **MCP spec churn** destabilizes the runtime | Med | Med | Not a Phase 0 concern (MCP is Phase 4); noted so the abstraction-behind-interface decision is remembered. |

The two **Critical × non-trivial-likelihood** risks (R1, R4) are exactly what Spikes A and B exist to retire. If Phase 0 does nothing else, it must answer those two.

---

## 7. Exit criteria (the Phase 0 gate)

Phase 0 is done when **all** of these are true (or a finding has explicitly forced an ADR revision):

- [ ] **Team-fit confirmed** (§4.0) — Python-first is right for this team, or ADR-0003 revised.
- [ ] **Spike A PASS** — the 3-node DAG runs, survives a mid-run `kill -9`, and resumes to **exactly-once** completion; parallel branches claim concurrently; Redis-wipe loses no state.
- [ ] **Spike B PASS** — Apollo enrich runs from a **manifest** against a live key; credential is envelope-encrypted at rest, unwrapped only in-memory; **redaction test passes**.
- [ ] **Spike C PASS** — `docker compose up` runs api+worker on Postgres+Redis in one command; an illegal cross-module import **fails CI**.
- [ ] **All 6 ADRs** moved from Proposed → Accepted-with-evidence (or revised with rationale).
- [ ] **Product validation note** (§5) written; ADR-0005 confirmed or flagged.
- [ ] **Phase 1 plan** drafted: the production engine, auth, single-org tenancy, vault, 5–8 real nodes, and the CLI — scoped against the `BLUEPRINT.md` §8 Phase 1 success criterion.
- [ ] **Spike code archived/deleted** — nothing from Phase 0 silently becomes production without a deliberate rewrite.

---

## 8. Timeline (indicative, 4 weeks)

| Week | Focus |
|---|---|
| **1** | §4.0 team-fit check (day 1). Start **Spike A** (the long pole). Stand up Postgres+Redis locally. Begin §5 customer conversations. |
| **2** | **Spike A** chaos test + idempotency. Start **Spike C** skeleton in parallel. Continue conversations. |
| **3** | **Spike B** (manifest + vault + redaction). Finish Spike C. Spike A hardening (Redis-wipe, concurrent-claim tests). |
| **4** | Convert ADRs to accepted-with-evidence. Write product-validation note. Draft the Phase 1 plan. Archive spike code. **Exit gate review.** |

**Sequencing rule:** Spike A starts first and is never blocked — it's the decision that everything else assumes. Spikes B and C can overlap it. The customer track runs the whole four weeks in parallel, owned by the founder/CEO, not the engineers.

---

## 9. Explicitly out of scope for Phase 0

To protect against R7 (scope creep), Phase 0 does **not** touch: the workflow builder UI, the marketplace, MCP, multi-org/teams, billing, more than one node, production-grade error handling, observability dashboards, or any optimization. Those are Phases 1–6. Phase 0 answers exactly four questions — *can we build the engine, can a node + vault work safely, can the monolith hold its seams, and is the team/wedge right* — and then stops.
