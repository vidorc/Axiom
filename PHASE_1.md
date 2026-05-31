# Axiom — Phase 1: Core Engine

**Status:** Committed (post-review)
**Window:** Weeks 5–12 (begins after the Phase 0 exit gate passes)
**Last updated:** 2026-05-31
**Audience:** The founding engineering team building the production core.

> **Phase 1's job is to turn the Phase 0 spikes into a production core that we run our own outbound on.** Phase 0 proved the architecture is viable with throwaway code; Phase 1 builds the real, tested, production-grade engine + vault + auth + a handful of real nodes + a CLI — and **we start dogfooding** (`BLUEPRINT.md` §8). The exit gate is a single, concrete, dishonest-to-fake criterion (§3). Everything in this plan exists to serve it.

Unlike Phase 0, **this code is permanent.** It is built TDD-first, type-safe, and to the `CODE QUALITY` bar in the brief. Spike code from Phase 0 is a reference, not a foundation — it gets rewritten, not promoted.

---

## 1. What Phase 1 is and is not

| Phase 1 IS | Phase 1 is NOT |
|---|---|
| The production custom orchestrator (DAG, parallel, retries, crash recovery, event log) | The workflow builder UI (Phase 3) |
| Auth + **single-org** tenancy, built with `org_id` discipline from day 1 | Multi-org / teams / RBAC roles beyond owner (Phase 5) |
| The production credential vault (envelope encryption) | A node sandbox (trust tiers; cloud sandbox is Phase 6) |
| The minimal node runtime (manifest + code kinds) | The MCP runtime (Phase 4) |
| 5–8 **real** first-party nodes that compose a cold-outbound workflow | The marketplace, templates as a product (Phase 5) |
| A CLI that runs a workflow defined in JSON/YAML, end-to-end | A web frontend of any kind |
| Scheduling + webhook triggers (lower priority than the engine core) | RAG, advanced AI, breadth of AI providers (deferred / cut) |
| **Dogfooding** our own outbound | Onboarding external users (Phase 3 launch) |

**Scope discipline:** if a task doesn't move the §3 success criterion or isn't a named in-scope item above, it waits. The biggest Phase-1 risk is gold-plating the engine instead of getting one real workflow running honestly end-to-end.

---

## 2. Goals

1. **A production orchestrator** that executes DAGs with parallel branches, retries, scheduling, webhook triggers, the append-only event log, and crash recovery — meeting the `<100 ms` orchestration-overhead target (`BLUEPRINT.md` §7).
2. **A production vault** that stores credentials under envelope encryption and unwraps them in-memory only, with provable no-plaintext-leakage (`SECURITY.md` §5).
3. **Auth + single-org tenancy** with `org_id` scoping enforced everywhere, so Phase 5 multi-org is *additive*, not a refactor (§7.1).
4. **A minimal but real node runtime** supporting `http_manifest` and `code` kinds, with the 2-method behavioral contract and the error taxonomy (`SDK_SPEC.md` §4).
5. **5–8 real nodes** that compose into a working cold-outbound workflow we actually use (§5).
6. **A CLI** that loads a workflow definition, runs it against live keys, streams status, and exposes run history + cost.
7. **Dogfooding from within the phase** — we run real outbound on Axiom and let the pain teach us.

---

## 3. The success criterion (the exit gate, stated once)

> **A real cold-outbound workflow runs end-to-end from the CLI against live provider keys — fanning out over a list of companies, enriching, personalizing with an AI node, and sending — with retries on transient failures, accurate per-node cost tracking, crash recovery proven by a chaos test, and a queryable run history. And we are using it for our own outbound.**

This is binary and hard to fake. If any clause is hand-waved (retries don't actually fire, cost is estimated not measured, the chaos test isn't in CI, or we're not actually dogfooding), Phase 1 is not done.

---

## 4. Workstreams

Six workstreams, tiered by how directly they gate §3. **Tier 1 is the critical path** and must not be blocked. Tier 2 is in-scope but yields to Tier 1 under pressure. Tier 3 is in-scope-if-time and explicitly droppable to Phase 1.5.

### Tier 1 — Critical path (the engine + what it needs to run one node)

#### WS-1: The orchestrator (the long pole)
The production rebuild of Phase 0 Spike A. Owns the run lifecycle (`DOMAIN_MODEL.md` §4).
- Ready-set evaluation against `node_state`; claim via `FOR UPDATE SKIP LOCKED`.
- Parallel branch execution (multiple ready rows, multiple workers).
- Per-node retry policy (graph `retry` block) driven by the error taxonomy; exponential backoff + jitter; honor `Retry-After`.
- Lease + reaper crash recovery; idempotency keyed `(run_id, node_id, attempt)`.
- Map/fan-out over rows (child `node_state` rows; fan back in at the join) — required for "split over companies."
- Append-only `execution_event` emission for every transition; Redis pub/sub fan-out.
- The `WorkflowEngine` interface (ADR-0001 §Decision) so a future Temporal escape hatch exists.

#### WS-2: The credential vault
Production envelope encryption (`SECURITY.md` §5).
- Pluggable KMS backend (KMS for cloud, file/env master key for self-host); **refuse to start with a placeholder key**.
- Encrypt-at-rest, unwrap-in-memory-at-exec, `last4`-only display, **no plaintext-returning path** anywhere (enforces "use ≠ read").
- The redacting `ctx.log` / `ctx.http` layer; a test asserting a known key value never appears in logs/events/DB.

#### WS-3: Minimal node runtime
The engine-side execution of a node (`SDK_SPEC.md` §4, §5).
- Manifest parser + executor for `http_manifest` (request, input/output mapping, `errors`→taxonomy, declarative pagination).
- `code` node loader honoring the 2-method contract (`validate`, `execute`, optional `compensate`).
- The `ExecutionContext` (`ctx.credentials` scoped to declared `auth`, `ctx.http`, `ctx.log`, `ctx.cost`, `ctx.cancel_token`).
- Per-provider rate-limit token buckets in Redis from `rate_limit_hint`.
- Node version pinning (`node_pins`) — even with a local registry, pins are immutable.

### Tier 2 — Required for "real" and for dogfooding

#### WS-4: Auth + single-org tenancy
- Email + OAuth (Google/GitHub) auth; hashed `api_token` for the CLI.
- **Single org per deployment**, but `org_id` on every tenant-scoped row and injected by the data-access layer from day 1 (§7.1).
- Owner role only in Phase 1; the `membership`/role *model* exists, the role *matrix* is Phase 5.
- RLS policies stubbed/enabled on tenant tables (measure overhead — `SECURITY.md` §4.2).

#### WS-5: The 5–8 real nodes
See §5 for the specific set and rationale. These are first-party (`axiom/*`), verified, and authored *through the SDK contract* to dogfood the SDK itself early — even though the public SDK polish is Phase 2.

#### WS-6: The CLI
- `axiom run <workflow.yaml>` — load, validate, enqueue, stream live status to the terminal.
- `axiom runs` / `axiom run <id>` — query history, per-node status, cost.
- `axiom creds add/list/rotate` — vault management (never reveals plaintext).
- Auth via `api_token`. This is the *only* user surface in Phase 1 and the proof of §3.

### Tier 3 — In-scope if time, else Phase 1.5

#### WS-7: Scheduling + webhook triggers
- `schedule` rows + a single leader-elected ticker (Redis lock) enqueuing due runs.
- `/triggers/{token}` webhook endpoint with signed tokens + HMAC verification + per-token rate limits (`SECURITY.md` §10.1).
- **Honest priority call:** the §3 criterion is *manual CLI trigger*. Scheduling/webhooks are genuinely useful and in-phase, but if the engine core (WS-1) runs long, these slip to a Phase 1.5 fast-follow without endangering the exit gate. Time-delays as scheduled re-entry (the drip-sequence mechanism, ADR-0001) ride on the same ticker and slip with it.

#### WS-8: Minimal observability
- The cost ledger (`cost_entry`) as a projection of events — **required** (it's in §3), so this slice is actually Tier 2.
- Structured logs + basic run metrics. The dashboard *UI* is Phase 3; Phase 1 exposes these via CLI + queryable tables only.

---

## 5. The node set (chosen to compose one real workflow)

The 5–8 nodes are not arbitrary — they are exactly what the dogfood cold-outbound workflow needs, and together they exercise every node-runtime capability. The target workflow:

```
[Load Companies]                         ← input: a CSV/list of domains (keyless — beats the cold-start wall)
       │
       ▼
[Apollo Enrich]  (http_manifest)         ← enrich person/company  → exercises: manifest, vault, cache, rate-limit
       │
       ▼
[Split / Map over rows]                  ← engine fan-out          → exercises: parallel claim, child node_states
   ┌───┴────────────────┐
   ▼                    ▼
[Scrape Site]      [HTTP (generic)]       ← context fetch / arbitrary API → exercises: code node, SSRF egress filter
   └───┬────────────────┘
       ▼
[AI Personalize] (OpenAI + Anthropic)    ← structured-output prompt → exercises: BYOK AI, JSON validation, cost-per-token
       │
       ▼
[Smartlead Send] (http_manifest)         ← outreach (side effect)   → exercises: terminal-vs-retryable errors, idempotency
```

| # | Node | Kind | Why it's in the set | Capability it proves |
|---|---|---|---|---|
| 1 | **Load Companies** (CSV/list input) | code | Keyless first input → a workflow runs with zero keys | Input mapping; the cold-start mitigation |
| 2 | **Apollo Enrich** | http_manifest | The canonical enrichment node; the Phase 0 Spike B node, productionized | Manifest path, vault scoping, enrichment cache, rate-limit |
| 3 | **HTTP (generic)** | http_manifest | The reference manifest node; *the* contributor template | Arbitrary API calls; the SDK's primary path |
| 4 | **Scrape Site** | code | Fetch + parse a public page for personalization context | Code-node contract; **SSRF egress filter** (`SECURITY.md` §8) |
| 5 | **AI Personalize (OpenAI)** | code | BYOK AI personalization with structured output | LLM call, JSON-schema validation, **cost-per-token** ledger |
| 6 | **AI Personalize (Anthropic)** | code | Prove provider-neutrality (two AI providers, one node shape) | BYOK breadth; shared AI-node abstraction |
| 7 | **Smartlead Send** | http_manifest | The outreach/side-effect terminus | **Idempotency on retry** of a real side effect; error taxonomy |
| 8 | *(stretch)* **Webhook Trigger** | — | Receive Smartlead reply events | WS-7; slips with scheduling if needed |

> **Scope honesty:** nodes 1–7 are the committed set (matches the brief's "HTTP, OpenAI, Anthropic, Apollo, one scraper, one outreach"). Node 8 rides Tier 3. We resist adding more — node *breadth* is Phase 2's job; Phase 1 needs *enough nodes to prove the engine on a real workflow*, not a catalog.

> **Authoring nodes through the SDK now de-risks Phase 2.** Even though the public SDK polish is Phase 2, we author these 7 against the real node contract — so we feel the SDK's rough edges as our own first contributor, and Phase 2 starts from evidence, not guesses.

---

## 6. Testing & dogfooding strategy

Reliability is principle #4 (`BLUEPRINT.md` §2); a workflow that silently double-sends is worse than a missing feature. Testing is not a phase-end activity here.

- **TDD for the engine.** The orchestrator's state transitions, ready-set logic, and retry/recovery paths are written test-first. The engine is the one place where "looks right" is not good enough.
- **The chaos test is promoted from spike to CI gate.** Phase 0 proved `kill -9` mid-run recovers to exactly-once; in Phase 1 that becomes an **automated CI test** that fails the build if recovery or idempotency regresses. This is the single most important test in the codebase.
- **Idempotency tests** on the real side-effecting node (Smartlead): inject a retry after the external call succeeds but before the state commit; assert exactly one send.
- **The no-leak test** (WS-2): a known credential value is asserted absent from all logs, events, and DB rows.
- **SSRF tests** (WS-3/Scrape node): assert that `169.254.169.254`, private ranges, and a public→private redirect are all blocked.
- **Property/load sanity:** confirm `SKIP LOCKED` throughput clears the bursty ~50/sec target on commodity hardware (not a benchmark suite — a sanity check).
- **Dogfooding is a test.** From the moment the cold-outbound workflow runs (mid-phase), we use it for our *actual* outbound. Every painful moment is a bug or a Phase-2 backlog item. If running our own outbound on Axiom is too painful for us, it is not ready for anyone.

---

## 7. Decisions & challenged assumptions

### 7.1 Single-org now, but built so multi-org is additive
Phase 1 is single-org, but **we do not take the shortcut of omitting `org_id`.** Every tenant-scoped table carries it; the data-access layer injects it; RLS is enabled. The cost is near-zero now and it means Phase 5 multi-org is *new rows and new role logic*, not a schema migration + a scoping retrofit across the whole codebase. **Challenged assumption:** "single-org means we can skip tenancy plumbing." Rejected — that shortcut is the classic source of a brutal Phase-5 rewrite.

### 7.2 Scheduling/webhooks are in-phase but droppable
The brief lists them in Phase 1. The §3 criterion does not require them. We keep them Tier 3 so the *engine core* never gets squeezed by trigger plumbing. Honest sequencing beats a checklist.

### 7.3 We author nodes against the real SDK contract before the SDK is "shipped"
Slight inversion of the roadmap (SDK is Phase 2), done deliberately: it makes us our own first node author and turns Phase 2's SDK work into refinement-with-evidence rather than design-in-a-vacuum.

### 7.4 Rollback/compensation stays minimal
Per `DOMAIN_MODEL.md` §4, `compensate()` is optional and defaults to `Unsupported`. Phase 1 ships the hook and the reverse-topological walk, but **none of the 7 nodes are required to implement meaningful compensation** — most (a sent email) can't. We build the mechanism, not fake guarantees.

### 7.5 No UI, and that's a feature
The CLI is the only surface. It forces the API and engine contracts to be clean and scriptable *before* a UI papers over them. The Phase 3 builder will be built on exactly the API the CLI uses.

---

## 8. Risk register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| P1-R1 | **Engine correctness** (double-execution, lost runs, recovery races) under real concurrency | Med | **Critical** | TDD + the promoted chaos test as a CI gate (§6). This is the phase's defining risk. |
| P1-R2 | **Gold-plating the engine** instead of running one real workflow | **High** | High | §1 scope discipline; the §3 criterion is the only north star; weekly check against it. |
| P1-R3 | **A real provider can't be a manifest** after all (escaped Phase 0's check) | Low–Med | High | Two of the 7 nodes are manifests (Apollo, Smartlead, HTTP); if one needs code, that's an ADR-0004 signal, surfaced loud. |
| P1-R4 | **Cost tracking is approximate, not measured** | Med | Med | Cost ledger is a projection of real `NodeSucceeded` events carrying actual usage; per-token/credit from the provider response, not estimates. |
| P1-R5 | **Single-org shortcut creates Phase-5 debt** | Med | High (later) | §7.1 — `org_id` + RLS from day 1. |
| P1-R6 | **Vault plaintext leak** | Med | **Critical** | WS-2 no-leak test in CI; no plaintext-returning code path exists to exploit. |
| P1-R7 | **Scheduling/webhooks eat the engine's time** | Med | Med | Tier 3 + explicit droppability to Phase 1.5 (§7.2). |
| P1-R8 | **Dogfooding doesn't actually happen** (we build but don't use) | Med | High | Dogfooding is a §3 exit clause, not a nice-to-have; the cold-outbound workflow is chosen to be *our* real workflow. |

---

## 9. Sequencing (indicative, weeks 5–12)

The engine (WS-1) starts immediately and is never blocked; the vault (WS-2) and node runtime (WS-3) converge on it; nodes + CLI make it real; triggers come last.

| Week | Focus |
|---|---|
| **5** | WS-1 production orchestrator skeleton (ready-set, claim, event emission), TDD from the first commit. WS-4 auth + `org_id` plumbing + DB schema/migrations. |
| **6** | WS-1 retries + lease/reaper + **chaos test promoted to CI**. WS-2 vault encrypt/unwrap + redaction + no-leak test. |
| **7** | WS-1 map/fan-out over rows. WS-3 manifest parser + `ExecutionContext` + rate-limit buckets. First node end-to-end: **Apollo Enrich** through the real vault. |
| **8** | WS-3 code-node loader + SSRF egress filter. Nodes: **HTTP generic**, **Scrape Site** (+ SSRF tests). WS-6 CLI `run` + live status streaming. |
| **9** | Nodes: **AI Personalize (OpenAI + Anthropic)** with structured output + cost-per-token. WS-8 cost ledger projection. |
| **10** | Node: **Smartlead Send** + **idempotency test** on the real side effect. CLI `runs`/history/cost. **First full cold-outbound run end-to-end.** |
| **11** | **Dogfooding begins** — run our own outbound; fix what hurts. WS-7 scheduling + webhook triggers *if* the engine is solid (else → Phase 1.5). |
| **12** | Hardening, the full test suite green (chaos + idempotency + no-leak + SSRF), exit-gate review, Phase 2 plan drafted. |

---

## 10. Definition of done (the Phase 1 gate)

Phase 1 is complete when **all** hold:

- [ ] **§3 criterion met:** the cold-outbound workflow runs end-to-end from the CLI against live keys, fanning out over companies, enriching, personalizing via an AI node, and sending.
- [ ] **Retries fire** on injected transient failures and respect the error taxonomy (retryable vs terminal).
- [ ] **The chaos test is in CI and green:** `kill -9` mid-run resumes to **exactly-once** completion; the idempotency test proves no double-send.
- [ ] **Cost is measured, not estimated:** the ledger reflects real per-node/per-token spend from provider responses, rolled up per run.
- [ ] **The no-leak test is green:** no credential plaintext in any log, event, or DB row; no API/CLI path reveals a secret.
- [ ] **SSRF tests are green:** metadata/private/redirect-to-private destinations are blocked.
- [ ] **`org_id` scoping + RLS** are present on every tenant-scoped table (Phase-5 readiness).
- [ ] **7 first-party nodes** authored through the real SDK contract and verified working.
- [ ] **We are dogfooding** — real outbound is running on Axiom.
- [ ] **Phase 2 plan drafted** (node runtime & public SDK), scoped against the `BLUEPRINT.md` §8 Phase 2 criterion (an external dev ships a node via manifest in < 30 min).

---

## 11. What Phase 1 hands to Phase 2

- A production engine the public node SDK can be built *against* (Phase 2 formalizes + documents what Phase 1 used internally).
- 7 real nodes that are simultaneously the SDK's first reference examples and the evidence for where the SDK's ergonomics need work.
- A clean API (the one the CLI uses) that the Phase 3 builder will sit on top of.
- A measured answer to "is the custom engine reliable?" — which either confirms ADR-0001 with production evidence or triggers its revisit before more is built on it.
