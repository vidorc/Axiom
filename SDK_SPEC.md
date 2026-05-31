# Axiom — Node SDK Specification

**Status:** Committed (post-review)
**Last updated:** 2026-05-31
**Audience:** Engineers building the node runtime; contributors authoring nodes.

> **The thesis this document serves.** The number of community integrations is roughly a function of how hard it is to write one ([ADR-0004](docs/adr/0004-http-manifest-nodes.md), [ADR-0006](docs/adr/0006-ecosystem-as-moat.md)). The SDK is therefore not a convenience layer — it is the **single highest-leverage investment in the moat.** The bar: *an external developer adds a working integration via a manifest in under 30 minutes, unaided.* If a design choice here trades 10 minutes of author time for 10 minutes of our time, the author wins.

This is a *specification*, not an implementation. Interface contracts and manifest shapes are shown as illustrative schemas — the contract, not the code that fulfills it.

---

## 1. Design goals & non-goals

### Goals
1. **No-code is the default path.** Most integrations are a manifest, not a program (§3).
2. **A tiny behavioral contract for code nodes** — two required methods, not five (§4).
3. **One artifact, many payoffs.** The manifest's I/O schema generates the UI form, type-checks edges at design time, documents the node, and powers cost/rate-limit accounting.
4. **Safe by construction.** The dominant node kind carries no arbitrary code; the contract makes credential scoping and redaction the default (`SECURITY.md`).
5. **Versioned and immutable.** Publishing a new version never alters a running workflow (§6).

### Non-goals (year 1)
- **A JS/TS code-node runtime.** Python only for code nodes (ADR-0003). Deferred, not foreclosed.
- **A node DSL / scripting language.** The manifest stays *declarative*; when logic is needed, you write a code node (§3.4). We will actively resist the manifest accreting conditionals/loops into an accidental programming language.
- **Arbitrary sandboxing for self-host.** Trust tiers handle this (`SECURITY.md` §6).

---

## 2. The three node kinds

Every node is one of three kinds. They share one behavioral contract (§4) and one manifest envelope (§5); they differ only in how `execute` is fulfilled.

| Kind | Author writes | Runs | Share of integrations | Trust |
|---|---|---|---|---|
| **`http_manifest`** | a declarative manifest (no code) | in-process, engine-interpreted | ~80% | Inherently safe (no code) |
| **`code`** | a manifest + a Python class | in-process (self-host/verified) or sandboxed (cloud unverified) | ~20% | Tiered (`SECURITY.md` §6) |
| **`mcp`** | nothing — discovered from a registered MCP server | via MCP client | ecosystem leverage | Untrusted remote (`SECURITY.md` §9) |

**The strategic point:** a contributor adds Apollo, Prospeo, or Smartlead by writing a *manifest*. They only drop to a code node for real logic — scraping, multi-step transforms, AI orchestration.

---

## 3. The HTTP-manifest node (the primary path)

A manifest describes "call this API with these inputs, map the response to these outputs." The engine does the rest: auth injection, the request, pagination, retries, rate-limiting, cost accounting, redaction.

### 3.1 Illustrative manifest

```yaml
# axiom/apollo-enrich@1.4.0  (apollo-enrich.node.yaml)
id: axiom/apollo-enrich
version: 1.4.0
kind: http_manifest
category: enrichment
manifest_schema: "1"          # the SDK manifest format version (§6.4) — NOT the node version

display:
  label: "Apollo — Enrich Person"
  icon: apollo
  color: "#1b2a4a"
  docs_url: https://docs.axiom.dev/nodes/apollo-enrich
  summary: "Enrich a person by email or LinkedIn URL via Apollo."

auth:                         # DECLARES credential need → vault scopes to exactly this (SECURITY §6.2)
  - provider: apollo
    inject:
      type: header
      name: "X-Api-Key"
      value: "{{ credentials.apollo }}"   # plaintext exists only in-memory at exec time

inputs:                       # JSONSchema → auto-generates the config form + type-checks edges
  type: object
  required: [email]
  properties:
    email:        { type: string, format: email, title: "Person email" }
    reveal_phone: { type: boolean, default: false, title: "Reveal phone number" }

request:
  method: POST
  url: "https://api.apollo.io/v1/people/match"
  body:
    json:
      email: "{{ inputs.email }}"
      reveal_phone_number: "{{ inputs.reveal_phone }}"
  pagination:                 # declarative — no code needed for paged endpoints
    type: none

outputs:                      # JSONSchema → enables design-time edge validation downstream
  type: object
  properties:
    full_name: { type: string,  from: "{{ response.person.name }}" }
    title:     { type: string,  from: "{{ response.person.title }}" }
    company:   { type: string,  from: "{{ response.person.organization.name }}" }
    linkedin:  { type: string,  from: "{{ response.person.linkedin_url }}" }

errors:                       # maps provider responses to the engine's retry taxonomy (§4.3)
  - when: { status: 429 }            then: { class: rate_limited, retryable: true }
  - when: { status: [500, 502, 503] } then: { class: provider_error, retryable: true }
  - when: { status: 422 }            then: { class: invalid_input, retryable: false }
  - when: { status: 401 }            then: { class: auth_error, retryable: false }

rate_limit_hint: { rpm: 600, concurrency: 5 }   # feeds the rate-limiter (Redis token bucket)
cost_model:      { unit: credit, per_call: 1 }   # feeds the cost ledger (DOMAIN_MODEL §8)
cache:           { key: "{{ inputs.email }}", ttl: 30d }   # opts into the enrichment cache
```

### 3.2 What the engine derives from one manifest
- **The builder config form** — rendered from `inputs` JSONSchema (no UI code per node).
- **Design-time edge type-checking** — `outputs` JSONSchema lets the builder verify that an upstream node can satisfy a downstream node's `inputs` *before* the workflow runs (`DOMAIN_MODEL.md` §3.1).
- **Credential scoping** — `auth.provider` is the *only* secret this node ever receives (`SECURITY.md` §6.2).
- **Retry behavior** — from `errors` + the per-node `retry` policy in the graph; the node author does not write retry logic.
- **Cost + rate limits + caching** — from the declarative blocks.

### 3.3 Templating & expressions
The `{{ }}` expression language is **deliberately minimal** — field access, the `inputs`/`credentials`/`response` namespaces, and a small set of safe formatting helpers (`upper`, `lower`, `default`, `json`). **No arbitrary code, no loops, no side effects.** When an author reaches for logic the expression language doesn't have, that is the signal to write a **code node** (§3.4) — not to grow the expression language. Holding this line is an explicit, ongoing discipline (ADR-0004 revisit trigger #2).

### 3.4 When to escalate to a code node
The manifest can't express: custom scraping/parsing, multi-request orchestration with branching, stateful transforms, or AI prompt construction beyond simple templating. Those are code nodes. The manifest still wraps the code node (same envelope), so the UI/cost/scoping payoffs are preserved.

---

## 4. The behavioral contract (code nodes)

The original brief specified five methods: `validate`, `execute`, `rollback`, `retry`, `metadata`. **We reduce this to two required + one optional**, because `retry` and `metadata` are *not* per-node behavior — they are engine policy and declarative manifest. Fewer required methods = lower contribution barrier, which is the whole game.

### 4.1 The interface (illustrative)

```python
# Contract shape — illustrative, not the implementation.
class Node:
    # REQUIRED — pure, no I/O, no side effects. Runs before any external call.
    # Returns Ok, or a ValidationError that fails the node WITHOUT spending money.
    def validate(self, inputs: Inputs) -> ValidationResult: ...

    # REQUIRED — the side-effecting call. Returns an Output or raises a typed NodeError.
    def execute(self, ctx: ExecutionContext, inputs: Inputs) -> Output: ...

    # OPTIONAL — best-effort compensation (saga step). Default: Unsupported.
    # Most GTM side effects (sent email) are NOT compensable — declaring this is honest, not lazy.
    def compensate(self, ctx: ExecutionContext, output: Output) -> CompensationResult:
        return CompensationResult.unsupported()
```

### 4.2 What we removed and why
| Brief method | Where it went | Why |
|---|---|---|
| `retry()` | **Engine policy** (graph `retry` block + `errors` taxonomy) | Per-node retry logic guarantees inconsistency. The engine retries uniformly based on declared error classes. |
| `metadata()` | **The manifest** (declarative) | Metadata is static data, not behavior. No reason to execute code to learn a node's name. |
| `rollback()` | Renamed `compensate()`, made **optional**, default `Unsupported` | "Rollback" implies a guarantee we can't keep. Compensation is best-effort and per-node-declared (`DOMAIN_MODEL.md` §4). |

### 4.3 Error taxonomy (the contract that makes retries work)
A node communicates failure via **typed error classes**, and the engine decides retry/terminal from the class — not from the node guessing.

| Class | `retryable` | Meaning | Engine action |
|---|:---:|---|---|
| `rate_limited` | ✓ | 429 / provider throttle | backoff (honor `Retry-After`) + retry |
| `provider_error` | ✓ | 5xx / transient upstream | exponential backoff + jitter, retry |
| `timeout` | ✓ | network/timeout | retry |
| `invalid_input` | ✗ | 4xx the input can't satisfy | terminal; surface to user |
| `auth_error` | ✗ | bad/expired credential | terminal; flag the credential |
| `not_found` | ✗ | resource absent | terminal (or skip, per edge policy) |
| `internal` | ✗ | bug in the node | terminal; logged for the author |

HTTP-manifest nodes map provider responses to these classes via the `errors` block (§3.1); code nodes raise the typed error directly.

### 4.4 The ExecutionContext (what the engine hands a node)

```python
# Illustrative — the capabilities a node is granted at execution time.
class ExecutionContext:
    credentials: CredentialAccessor   # ctx.credentials("apollo") → plaintext, in-memory only,
                                       #   ONLY for providers the manifest `auth` declared (SECURITY §6.2)
    http: InstrumentedHttpClient      # pre-wired: redaction, rate-limit buckets, retry, egress filter,
                                       #   timeouts. Authors SHOULD use this, not a raw client.
    log: StructuredLogger             # ctx.log(level, msg, **fields) — REDACTING (SECURITY §5.4);
                                       #   raw print/stdout is captured + redaction-scanned too.
    cost: CostReporter                # ctx.cost(amount, unit) → cost ledger (DOMAIN_MODEL §8)
    cache: EnrichmentCache            # ctx.cache.get/set — per-org, the data-gravity feature
    cancel_token: CancelToken         # cooperative cancellation; check between steps
    run: RunInfo                      # read-only: run_id, node_id, attempt, org_id (NO cross-node mutable state)
```

**The context is the security boundary made ergonomic:** using `ctx.http` gives an author redaction, egress protection, and rate-limiting *for free* — the safe path is the easy path. There is intentionally **no shared mutable state** between nodes; data flows only through declared edges + the event log (`DOMAIN_MODEL.md` §4).

---

## 5. The manifest envelope (common to all kinds)

Every node — manifest, code, or MCP — is described by a manifest with this common envelope. Code nodes add an `entrypoint`; MCP nodes are generated from tool schemas.

```yaml
id: <namespace>/<name>         # globally unique, npm/docker-style
version: <semver>              # immutable once published (§6)
kind: http_manifest | code | mcp
manifest_schema: "1"           # SDK format version (§6.4)
category: scraping|enrichment|ai|outreach|db|http|custom
display: { label, icon, color, docs_url, summary }
auth: [ { provider, scopes?, inject? } ]   # declared credential need (drives scoping)
inputs:  <JSONSchema>          # form generation + edge type-checking
outputs: <JSONSchema>          # edge type-checking
rate_limit_hint: { rpm, concurrency }
cost_model: { unit, per_call | per_token | per_row }
cache: { key, ttl }?           # optional opt-in to the enrichment cache

# kind: http_manifest →  request: {...}, pagination: {...}, errors: [...]
# kind: code          →  entrypoint: "module:ClassName", runtime: "python3.12", dependencies: [...]
# kind: mcp           →  (auto-generated: mcp_server_ref, tool_name, schemas)
```

The envelope is the **stable, versioned contract** (§6.4). Adding optional fields is backward-compatible; changing the meaning of an existing field is a manifest-schema-version bump.

---

## 6. Versioning strategy

Versioning is where reliability lives. The invariant: **publishing a new node version never changes the behavior of a workflow already pinned to an older one.**

### 6.1 Node versions are SemVer + immutable
- `MAJOR.MINOR.PATCH`. Once published, a version's manifest + artifact are **immutable and signed** (`node_version.signature`, `DOMAIN_MODEL.md` §6).
- **MAJOR** = breaking I/O schema or behavior change. **MINOR** = backward-compatible additions (new optional input/output). **PATCH** = fixes that don't change the contract.

### 6.2 Workflows pin exact versions
A `workflow_version` stores `node_pins: {graph_node_id → node_version_id}` (`DOMAIN_MODEL.md` §3). At execution, the engine loads the *pinned* version. A node author shipping `@2.0.0` cannot break a production workflow on `@1.4.0` — full stop.

### 6.3 Upgrades are explicit and assisted
- The builder surfaces "a newer version is available" and offers an **upgrade action** that creates a *new* workflow version with the new pin — never an in-place mutation.
- For MAJOR upgrades, the builder uses the two JSONSchemas to **diff the I/O contract** and flag exactly which edges/config break, so the user upgrades with eyes open.

### 6.4 The manifest *format* is itself versioned
`manifest_schema: "1"` versions the SDK's manifest format, independently of any node's version. This lets the SDK evolve (add capabilities) while old manifests keep parsing. A manifest-schema MAJOR bump is a rare, deliberate, migration-supported event — distinct from the routine node-version churn.

### 6.5 Deprecation
A version can be flagged `deprecated` (warns on new use) without being deleted — existing pins keep resolving. Hard removal is reserved for security takedowns (§7.4) and is loud and audited.

---

## 7. Publishing model

The path from "I wrote a node" to "others can install it," and the trust gates along it.

### 7.1 Namespaces & identity
- `axiom/*` is reserved for first-party, verified nodes.
- Contributors publish under their handle/org namespace (`acme/*`). Namespace = attribution = the reputation that motivates contribution (ADR-0006).

### 7.2 Publish flow
```
author writes manifest (+ code for code-nodes)
   → `axiom validate`   (CLI: lints manifest, checks JSONSchemas, dry-runs against a sample)
   → `axiom publish`    → registry ingest:
        • schema validation (manifest_schema conformance)
        • signature generation (supply-chain integrity, SECURITY §6.4)
        • immutability lock on (namespace/name@version)
        • marketplace_listing created (attribution, install_count starts at 0)
   → installable by any org via `org_installed_node`
```

### 7.3 Verification tiers
- **Unverified** (default for community): installable, runs in-process **only on self-host** (T-C) and is **disallowed for unverified *code* on Cloud** until the Phase 6 sandbox exists (`SECURITY.md` §6.1). Unverified *manifest* nodes (no code) are safe and cloud-eligible.
- **Verified**: human + automated review → eligible to run in-process as trusted (T-A) and on Cloud. The `verified` badge is the marketplace trust signal.

### 7.4 Takedown & security response
A version found malicious can be **quarantined** (blocked from new installs/runs) and, in severe cases, hard-removed — both are loud, audited events. Because workflows pin versions, takedown is the one case where an existing pin is forcibly invalidated; users are notified.

### 7.5 The local registry first
In year 1 the registry is **local/self-hostable** (nodes resolve from the org's installed set + a curated first-party index). The public, hosted, social marketplace is Phase 5 (`BLUEPRINT.md` §8). The SDK contract is identical in both — only the distribution surface changes.

---

## 8. Developer experience (the 30-minute test)

This is the acceptance criterion for the SDK, tested in Phase 2 with a **real external developer** (`BLUEPRINT.md` §8):

1. `axiom init node` scaffolds a manifest with inline docs and a worked example.
2. The author fills in `request`, `inputs`, `outputs`, `auth` — no engine knowledge required.
3. `axiom validate` lints + dry-runs against a sample payload, with actionable errors ("`outputs.company` path `response.org.name` not found in sample response").
4. `axiom test` runs the node against a real credential locally.
5. `axiom publish` ships it.

If a competent developer can't go from zero to a published, working enrichment node in under 30 minutes **without asking us a question**, the SDK has failed its core job — and that's a Phase 2 blocker, not a polish item.

### What makes or breaks the 30 minutes
- **Great error messages** from `validate` (the difference between 10 minutes and an abandoned afternoon).
- **A worked example in the scaffold**, not a blank file.
- **The JSONSchema-driven form** means authors never touch UI.
- **`ctx.http` doing the right thing by default** means code-node authors don't think about retries, redaction, or egress.

---

## 9. Open questions for Phase 0 / Phase 2

These are deliberately unresolved and must be settled with spikes, not in this doc:

1. **Expression language scope.** Exactly which helpers ship in `{{ }}`? Err minimal; every addition is pressure toward an accidental DSL (ADR-0004).
2. **Pagination coverage.** How many real provider pagination styles (cursor, offset, link-header, token) can the *declarative* `pagination` block cover before authors must drop to code? Survey 10 real GTM APIs in Phase 2.
3. **Code-node dependency model.** How are a Python code node's third-party deps declared, pinned, scanned, and (on Cloud) sandboxed? Ties directly to `SECURITY.md` §6.4.
4. **Map/fan-out at the node level.** Is "run this node per row" a manifest property, an engine-level map node, or both? (`DOMAIN_MODEL.md` §4 fan-out.)
5. **Versioned migration helpers.** When a node ships a MAJOR, can the author provide an optional input-mapping migration to ease workflow upgrades (§6.3)?
