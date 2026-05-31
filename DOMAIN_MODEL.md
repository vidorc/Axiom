# Axiom — Domain Model

**Status:** Committed (post-review)
**Last updated:** 2026-05-31
**Audience:** Engineers implementing the data layer, the engine, and the SDK.

This document defines Axiom's domain entities, their relationships, and the four models that carry the most design weight: **execution**, **credentials**, **nodes**, and **marketplace**. It is the bridge between `BLUEPRINT.md` (the *why*) and the eventual schema/code (the *how*).

It is **not** DDL. Field lists are conceptual; types are indicative (`uuid`, `jsonb`, `citext`, `timestamptz`). Where a decision is load-bearing, the rationale is inline. Where it links to a deeper doc (`SECURITY.md`, `SDK_SPEC.md`), the detail lives there.

### Modeling conventions

- **Every tenant-scoped entity carries `org_id`.** This is the multi-tenancy spine. Tenant isolation is enforced at the data-access layer with Row-Level Security (RLS) as a backstop — see `SECURITY.md`.
- **IDs are UUIDs** (v7 where ordering helps index locality), except the event log which uses a `bigint` monotonic sequence per execution.
- **Time is `timestamptz`, always UTC.**
- **Soft state vs. immutable state.** Authoring entities are mutable + versioned; execution entities are append-mostly; the event log is strictly append-only.
- **Large payloads never live in Postgres.** They go to object storage; the row holds a reference (`*_ref`). See §3.4.

---

## 1. Entity catalog (the map)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ IDENTITY & TENANCY                                                          │
│   organization 1───* membership *───1 user                                  │
│   organization 1───* api_token                                              │
├──────────────────────────────────────────────────────────────────────────┤
│ CREDENTIAL VAULT                                                            │
│   organization 1───* credential   (envelope-encrypted; see §5)              │
├──────────────────────────────────────────────────────────────────────────┤
│ WORKFLOW AUTHORING                                                          │
│   organization 1───* workflow 1───* workflow_version (immutable)            │
│                                  └─ pins node_versions + mcp tools          │
│   organization 1───* schedule ──▶ workflow                                  │
│   organization 1───* trigger   ──▶ workflow   (webhook/event)               │
├──────────────────────────────────────────────────────────────────────────┤
│ EXECUTION / ORCHESTRATION                                                   │
│   workflow_version 1───* execution 1───* node_state                         │
│   execution 1───* execution_event   (append-only log; see §4)               │
│   node_state ──▶ output_ref (object store)                                  │
├──────────────────────────────────────────────────────────────────────────┤
│ NODE RUNTIME                                                                │
│   node 1───* node_version (immutable, semver)                               │
│   organization *───* node_version  (via org_installed_node)                 │
│   organization 1───* mcp_server 1───* mcp_tool (discovered)                 │
├──────────────────────────────────────────────────────────────────────────┤
│ OBSERVABILITY & COST                                                        │
│   execution 1───* cost_entry   (ledger; projection of events)               │
│   organization 1───* audit_event (append-only)                              │
├──────────────────────────────────────────────────────────────────────────┤
│ MARKETPLACE & DATA GRAVITY                                                  │
│   marketplace_listing ──▶ (node | template)                                 │
│   organization 1───* template ──▶ workflow_version snapshot                 │
│   organization 1───* enrichment_cache_entry                                 │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Identity & tenancy

The root of every scope. Get this wrong and isolation leaks everywhere; it is the highest-stakes boundary in the system.

### `organization`
The tenant. Everything else hangs off it.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `name` | text | |
| `slug` | citext (unique) | URL-safe, case-insensitive |
| `plan` | enum(`free`,`team`,`agency`,`enterprise`) | drives cloud limits/billing |
| `parent_org_id` | uuid (fk, nullable) | **agency multi-client** — see §2.1 |
| `created_at` | timestamptz | |

### `user`
A person. Identity is global; *authorization* is always via `membership` (a user alone has no permissions).

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `email` | citext (unique) | |
| `auth_provider` | enum(`email`,`google`,`github`) | |
| `created_at` | timestamptz | |

### `membership` — the tenancy + RBAC join
A user's relationship to one org. **All authorization decisions read this row.**

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `org_id` | uuid (fk) | |
| `user_id` | uuid (fk) | |
| `role` | enum(`owner`,`admin`,`member`) | see RBAC matrix below |
| `created_at` | timestamptz | |

**Indexes:** `(org_id, user_id)` unique; `(user_id)` for "my orgs" lookup.

**RBAC matrix** (full detail in `SECURITY.md`):

| Capability | owner | admin | member |
|---|:---:|:---:|:---:|
| Manage billing / delete org | ✓ | | |
| Manage members & roles | ✓ | ✓ | |
| Create/edit/delete workflows | ✓ | ✓ | ✓ |
| Run workflows | ✓ | ✓ | ✓ |
| Add/rotate credentials | ✓ | ✓ | |
| **Use** a credential in a workflow | ✓ | ✓ | ✓ |
| **Read** a credential's value | — | — | — |
| Install/publish nodes | ✓ | ✓ | |
| Register MCP servers + set permissions | ✓ | ✓ | |

> **The load-bearing nuance:** *no role can read a credential's plaintext value.* `member` (and everyone else) can **invoke** a credential inside a workflow but never **see** or **export** it. The permission is "use," not "read." The original brief missed this; it is central to the BYOK trust story.

### `api_token`
Programmatic access (CLI, CI, the SDK). Scoped to an org, carries a role, hashed at rest.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `org_id` | uuid (fk) | |
| `name` | text | |
| `token_hash` | bytea | never store the plaintext token |
| `role` | enum | mirrors membership roles |
| `last_used_at` | timestamptz | |
| `expires_at` | timestamptz (nullable) | |

### 2.1 Agency multi-client structure

Agencies are the wedge ([ADR-0005](docs/adr/0005-agencies-as-initial-wedge.md)), so the model supports a **parent org → client sub-orgs** hierarchy via `organization.parent_org_id`:

- Each **client** is its own `organization` (own credentials, own data, own isolation boundary — a client's Apollo key is *never* visible to a sibling client).
- The **agency** parent org holds memberships that can be granted *delegated* access into client sub-orgs.
- Templates (§7) flow from the agency down to clients for repeatable delivery.

This keeps the isolation guarantee absolute (sub-orgs are full tenants) while enabling the agency's cross-client operations through explicit, audited delegation — not by weakening tenant boundaries.

---

## 3. Workflow authoring vs. execution (the central split)

The single most important structural decision in the domain: **design-time and run-time are different entities with different lifecycles.**

- **Authoring** = `workflow` + `workflow_version`. Mutable design, immutable versions.
- **Execution** = `execution` + `node_state` + `execution_event`. A run *pins* the exact `workflow_version` it started with.

This means **editing a workflow can never alter a run already in flight.** It also means the engine can evolve without touching the editor, and vice versa.

### `workflow`
The mutable container / "current pointer."

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `org_id` | uuid (fk) | |
| `name` | text | |
| `folder` | text (nullable) | lightweight organization |
| `current_version_id` | uuid (fk → workflow_version) | the editable HEAD |
| `created_by` | uuid (fk → user) | |
| `created_at` | timestamptz | |

### `workflow_version` — immutable, append-only
A frozen snapshot of the graph. New edits create a new version; old versions are never mutated.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `workflow_id` | uuid (fk) | |
| `version` | int | monotonic per workflow |
| `graph` | jsonb | nodes + edges + per-node config (see §3.1) |
| `node_pins` | jsonb | `{graph_node_id → node_version_id}` — exact versions |
| `created_by` | uuid (fk) | |
| `created_at` | timestamptz | |

**Indexes:** `(workflow_id, version)` unique.

**Why `node_pins` matters:** a node author shipping `apollo-enrich@2.0.0` must **never** silently change the behavior of a running production workflow pinned to `@1.4.0`. The pin is the contract. See `SDK_SPEC.md` versioning.

### 3.1 The `graph` shape (conceptual)

```jsonc
{
  "nodes": [
    {
      "id": "n1",                      // graph-local node id (stable within the workflow)
      "node_ref": "axiom/apollo-enrich", // which registry node
      "config": { /* validated against the node's input JSONSchema */ },
      "credentials": { "apollo": "cred_uuid" }, // binds a vault credential by id
      "retry": { "max_attempts": 3, "backoff": "exponential", "retry_on": ["429","503"] },
      "position": { "x": 240, "y": 120 } // UI only; ignored by the engine
    }
  ],
  "edges": [
    {
      "from": "n1", "to": "n2",
      "mapping": { /* expression/JSONPath: how n1's output feeds n2's input */ },
      "condition": null               // optional: only traverse if expression is truthy
    }
  ]
}
```

**Validation happens at authoring time, not run time:** cycle detection, edge type-checking (does `n1.output` satisfy `n2.input` JSONSchema?), credential-reference-in-org checks, node-version-installed checks. A workflow that won't run should fail to *save* (or save with explicit warnings), never fail mysteriously mid-execution.

### `schedule`
Time-based triggers. A single leader-elected ticker enqueues due runs (no distributed cron in v1).

| Field | Type | Notes |
|---|---|---|
| `id` / `org_id` / `workflow_id` | uuid | |
| `cron` | text | standard cron expression |
| `timezone` | text | IANA tz |
| `next_run_at` | timestamptz | |
| `enabled` | bool | |

**Index:** `(enabled, next_run_at)` — the ticker's hot query.

### `trigger`
Event/webhook entry points. Each exposes a signed URL `/triggers/{token}`.

| Field | Type | Notes |
|---|---|---|
| `id` / `org_id` / `workflow_id` | uuid | |
| `type` | enum(`webhook`,`event`) | |
| `token` | text (unique, signed) | the secret in the URL |
| `secret` | bytea | HMAC verification for known providers (e.g. Smartlead reply webhooks) |
| `enabled` | bool | |

---

## 4. Workflow execution model

The run-time core. Built on three entities — `execution`, `node_state`, `execution_event` — and the principle that **execution is an event log; everything else is a projection** (see `BLUEPRINT.md` §3.3).

### `execution` (a "run")
One invocation of a `workflow_version`.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `org_id` | uuid (fk) | |
| `workflow_id` | uuid (fk) | denormalized for history queries |
| `workflow_version_id` | uuid (fk) | **the pinned definition this run uses** |
| `status` | enum(`queued`,`running`,`succeeded`,`failed`,`cancelled`) | |
| `trigger` | enum(`manual`,`schedule`,`webhook`,`event`) | |
| `input` | jsonb (or ref) | trigger payload |
| `total_cost_cents` | int | rolled up from cost_entry |
| `stats` | jsonb | node counts, duration, etc. |
| `started_at` / `finished_at` | timestamptz | |

**Indexes:** `(org_id, workflow_id, started_at desc)` for run history; a **partial** index on `(status)` `WHERE status IN ('queued','running')` for the scheduler/reaper hot path.

### `node_state` — the orchestrator's work table
One row per node per run. This is what workers claim and mutate.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `execution_id` | uuid (fk) | |
| `node_id` | text | graph-local id (`n1`) |
| `status` | enum(`pending`,`ready`,`running`,`succeeded`,`failed`,`skipped`,`compensating`,`compensated`) | |
| `attempt` | int | retry counter |
| `input_ref` | text/jsonb | inline if small, object-store ref if large |
| `output_ref` | text/jsonb | same |
| `cost_cents` | int | |
| `lease_until` | timestamptz (nullable) | **crash recovery** — see below |
| `error` | jsonb (nullable) | typed error (taxonomy in `SDK_SPEC.md`) |
| `started_at` / `finished_at` | timestamptz | |

**Indexes:** `(execution_id, node_id)` unique; `(status, lease_until)` for claim + reap.

### The execution algorithm (how it actually runs)

```
1. RunRequested  → create `execution` (queued) + one `node_state` per graph node (pending)
                   + append RunRequested event.
2. RunStarted    → orchestrator computes the READY set:
                   a node is READY when every inbound edge is satisfied
                   (upstream succeeded, or edge.condition truthy, or no inbound edges = root).
3. CLAIM         → a worker runs:
                     SELECT ... FROM node_state
                     WHERE status='ready' AND (lease_until IS NULL OR lease_until < now())
                     FOR UPDATE SKIP LOCKED LIMIT 1;
                   sets status='running', lease_until = now() + lease_ttl, attempt += 1.
                   → SKIP LOCKED gives a correct concurrent queue with no extra component;
                     parallel branches are just multiple READY rows claimed by multiple workers.
4. EXECUTE       → load node@pinned_version → resolve inputs via edge mappings
                   → unwrap declared credentials (in-memory) → ctx.cache check
                   → validate() → execute() → record cost, output_ref.
5. SETTLE        → success: status='succeeded', append NodeSucceeded(output_ref),
                             recompute READY set (may unblock downstream).
                   failure: if attempt < max → status='ready' after backoff (retry);
                            else status='failed' → run fails (or branch, per policy).
6. RECOVER       → a reaper periodically finds running rows with expired lease_until
                   and resets them to 'ready' (the worker died). Durable state means the
                   run resumes exactly where it stopped; idempotency key (run_id,node_id,attempt)
                   ensures no double side-effects.
7. FINISH        → when no node is pending/ready/running: terminal status; append RunSucceeded
                   / RunFailed / RunCancelled.
```

### `execution_event` — append-only log
The source of truth for *what happened*. History, the live viewer, the audit trail, and the cost ledger are all projections of this.

| Field | Type | Notes |
|---|---|---|
| `id` | bigint (pk) | global monotonic |
| `execution_id` | uuid (fk) | |
| `org_id` | uuid (fk) | for partition/scoping |
| `seq` | int | per-execution ordering |
| `type` | enum | `RunRequested`,`RunStarted`,`NodeReady`,`NodeStarted`,`NodeRetried`,`NodeSucceeded`,`NodeFailed`,`NodeCompensating`,`NodeCompensated`,`RunSucceeded`,`RunFailed`,`RunCancelled` |
| `node_id` | text (nullable) | |
| `attempt` | int (nullable) | |
| `payload` | jsonb / ref | small inline, large by reference |
| `created_at` | timestamptz | |

**Index:** `(execution_id, seq)`. **Scaling:** partition by `created_at` (monthly) once volume warrants; archive cold partitions to object storage (`BLUEPRINT.md` §7).

### Retries, compensation, and cancellation

- **Retries** are *engine policy*, declared per-node in the graph (`retry` block), not implemented by each node. The engine distinguishes **retryable** (429/503/timeout) from **terminal** (400/auth) errors using the node's error taxonomy (`SDK_SPEC.md`). Exponential backoff + jitter.
- **Compensation (rollback)** is **best-effort and opt-in**, *not* a transactional guarantee — you cannot un-send an email. On run failure with rollback enabled, the engine walks *succeeded* nodes in reverse topological order calling the optional `compensate()` hook; nodes that can't compensate declare `Unsupported`. **Deprioritized for the MVP** (`BLUEPRINT.md` §3.2) — most GTM side effects aren't compensable, so we are honest about it rather than pretending.
- **Cancellation** sets a cooperative `cancel_token` the node observes; in-flight external calls are allowed to finish or time out, never hard-killed mid-write.

### Fan-out over rows

GTM workflows fan out over *rows* (companies, leads). A **map node** spawns child `node_state` rows — one per input item — that the ready-set treats as independent claimable units, fanning back in at the downstream join. This is how "split → enrich each → personalize each → send" parallelizes naturally on the `SKIP LOCKED` queue.

---

## 5. Credential model

The vault. The full security treatment is in `SECURITY.md`; here is the *domain* shape and the rules that bind it to execution.

### `credential`

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `org_id` | uuid (fk) | strictly org-scoped; RLS-enforced |
| `provider` | text | `apollo`,`openai`,`smartlead`,… |
| `label` | text | human display name |
| `ciphertext` | bytea | the secret, **envelope-encrypted** (DEK wrapped by KMS KEK) |
| `dek_id` | text | which data-encryption-key wrapped this |
| `last4` | text | display only — e.g. `…a1b2`; never the full value |
| `created_by` | uuid (fk) | |
| `created_at` / `rotated_at` | timestamptz | |

**Index:** `(org_id, provider)`. **Never** index, log, or project `ciphertext`.

### Credential rules (domain invariants)

1. **Plaintext is never stored, never logged, never returned by any API.** Display surfaces show `label` + `last4` only.
2. **Bound by reference, not by value.** A graph node references a credential by `id` (`config.credentials.apollo = "cred_uuid"`). The plaintext is unwrapped *in worker memory at execution time only*, then discarded.
3. **Scoped to declared need.** A node receives *only* the credentials its manifest declares (`SDK_SPEC.md` → `auth`). A node that declares only `apollo` can never be handed the `openai` key, even if both exist in the org. This caps the blast radius of a malicious or buggy node.
4. **Use ≠ read.** Per the RBAC matrix, members can bind/use a credential in a workflow but no role can retrieve its plaintext.
5. **Cross-org references are rejected at load time** — a graph that references a credential `id` outside its org fails validation, not just the query layer.

---

## 6. Node model

A node is the unit of action. Three kinds, one behavioral contract (`SDK_SPEC.md` is the full spec; this is the domain/registry shape). See [ADR-0004](docs/adr/0004-http-manifest-nodes.md).

### `node` (registry entry — the "package")

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `namespace` | text | `axiom`, or a contributor handle |
| `name` | text | `apollo-enrich` |
| `kind` | enum(`http_manifest`,`code`,`mcp`) | the three node kinds |
| `category` | enum(`scraping`,`enrichment`,`ai`,`outreach`,`db`,`http`,`custom`) | |
| `latest_version` | semver | |
| `verified` | bool | first-party / reviewed → eligible for in-process + cloud trust tier |
| `author_org_id` | uuid (fk, nullable) | null for first-party |
| `install_count` | int | marketplace signal |
| `created_at` | timestamptz | |

**Index:** `(namespace, name)` unique; `(category, verified)`.

### `node_version` — immutable, semver

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `node_id` | uuid (fk) | |
| `version` | semver | immutable once published |
| `manifest` | jsonb | the full node manifest (`SDK_SPEC.md`) |
| `artifact_ref` | text (nullable) | for code nodes: the packaged code; null for pure manifest |
| `signature` | bytea | publisher signature (supply-chain integrity) |
| `deprecated` | bool | flagged but existing pins keep working |
| `published_at` | timestamptz | |

**Index:** `(node_id, version)` unique.

### `org_installed_node`
Which node versions an org may use. Installing pins availability without forcing upgrades.

| Field | Type | Notes |
|---|---|---|
| `org_id` | uuid (fk) | |
| `node_version_id` | uuid (fk) | |
| `installed_at` | timestamptz | |

### The node manifest (conceptual — full spec in `SDK_SPEC.md`)

```jsonc
{
  "id": "axiom/apollo-enrich",
  "version": "1.4.0",
  "kind": "http_manifest",
  "category": "enrichment",
  "inputs":  { /* JSONSchema → auto-generates the config form + type-checks edges */ },
  "outputs": { /* JSONSchema → enables design-time edge validation */ },
  "auth":    [ { "provider": "apollo", "scopes": [] } ],   // declares credential need (§5 rule 3)
  "rate_limit_hint": { "rpm": 600, "concurrency": 5 },
  "cost_model": { "unit": "credit", "per_call": 1 },        // feeds the cost ledger (§8)
  "ui": { "icon": "apollo", "color": "#…", "label": "Apollo Enrich", "docs_url": "…" }
}
```

### MCP as a node kind

An MCP server is modeled as a *source of nodes*, not a separate universe ([ADR-0006](docs/adr/0006-ecosystem-as-moat.md)).

#### `mcp_server`

| Field | Type | Notes |
|---|---|---|
| `id` / `org_id` | uuid | |
| `name` | text | |
| `transport` | enum(`stdio`,`http`) | `stdio` = self-host only; cloud defaults to `http`/SSE |
| `endpoint` | text | URL or command |
| `auth_credential_id` | uuid (fk → credential, nullable) | reuses the vault |
| `status` | enum(`healthy`,`degraded`,`down`) | from health monitor |
| `allowed_tools` | jsonb | admin allow-list (default-deny) |
| `last_health_at` | timestamptz | |

#### `mcp_tool` (discovered)
On registration, the MCP client handshake lists tools; each tool's schema maps to a node manifest. These appear in the node library like any other node, gated by `allowed_tools`.

| Field | Type | Notes |
|---|---|---|
| `id` / `mcp_server_id` | uuid | |
| `tool_name` | text | |
| `input_schema` / `output_schema` | jsonb | normalized into manifest I/O |
| `enabled` | bool | reflects allow-list |

---

## 7. Marketplace & data-gravity models

The flywheel surfaces ([ADR-0006](docs/adr/0006-ecosystem-as-moat.md)). Thin in v1, but the model is laid out now so it accretes value coherently.

### `marketplace_listing`
A browsable, installable entry pointing at either a node or a template.

| Field | Type | Notes |
|---|---|---|
| `id` | uuid (pk) | |
| `type` | enum(`node`,`template`) | |
| `ref_id` | uuid | → `node` or `template` |
| `title` / `description` | text | |
| `author_org_id` | uuid (fk, nullable) | attribution = contributor reputation |
| `install_count` | int | social proof + ranking signal |
| `featured` | bool | curation |
| `created_at` | timestamptz | |

### `template` — a shareable workflow
Templates drive activation harder than individual nodes (a new user wants a working workflow, not a parts bin) and are a first-class contribution.

| Field | Type | Notes |
|---|---|---|
| `id` / `org_id` | uuid | |
| `name` / `description` | text | |
| `graph_snapshot` | jsonb | a `workflow_version.graph` with credentials *stripped* and node refs preserved |
| `required_nodes` | jsonb | node refs a user must have installed |
| `required_providers` | jsonb | which provider keys the user must supply (BYOK) |
| `created_by` | uuid (fk) | |

> **Security note:** a template's `graph_snapshot` must have **all credential bindings stripped** before sharing — a template carries the *shape* of a workflow, never anyone's keys. Enforced at template-creation time.

### `enrichment_cache_entry` — the data-gravity keystone
The feature that is *both* a cost-saver (the ADR-0005 wedge) *and* a lock-in (the ADR-0006 moat): never re-pay a provider for data you already fetched.

| Field | Type | Notes |
|---|---|---|
| `org_id` | uuid (fk) | cache is **per-org** — never share enriched data across tenants |
| `provider` | text | |
| `key_hash` | text | hash of the lookup key (e.g. domain, email) |
| `value` | jsonb / ref | the cached result |
| `fetched_at` | timestamptz | |
| `ttl` | interval | staleness policy per provider/data type |

**Index:** `(org_id, provider, key_hash)` unique. **Isolation invariant:** the cache is strictly org-scoped — one tenant's enriched data is *never* served to another, even on a cache hit. (A naive global cache would be a data-leak and a ToS violation.)

---

## 8. Observability & cost (projections, not sources)

These are **read models built from `execution_event`**, never on the live execution path — analytics load can never threaten execution.

### `cost_entry` (ledger)
One entry per costed node execution, projected from `NodeSucceeded` events + the node's `cost_model`.

| Field | Type | Notes |
|---|---|---|
| `id` / `execution_id` / `org_id` | uuid | |
| `node_id` | text | |
| `provider` | text | |
| `amount_cents` | int | normalized cost |
| `unit` / `quantity` | text / numeric | tokens, credits, calls |
| `created_at` | timestamptz | |

Rolls up into `execution.total_cost_cents` and powers the dashboard's **API cost / provider usage / failure-rate** views.

### `audit_event` — append-only
Security-relevant actions (credential created/used/rotated, role changed, MCP server added, workflow run). Same append-only infrastructure as the event log; never mutated. Detailed in `SECURITY.md`.

---

## 9. Relationship summary & cardinalities

| Relationship | Cardinality | Lifecycle note |
|---|---|---|
| organization → membership → user | 1—*—1 (M:N via membership) | membership carries the role |
| organization → credential | 1—* | org-scoped, RLS-enforced, never cross-org |
| organization → workflow → workflow_version | 1—*, 1—* | version is immutable; workflow points at current |
| workflow_version → execution | 1—* | execution pins the version |
| execution → node_state | 1—* | one per graph node (+ children for map fan-out) |
| execution → execution_event | 1—* | append-only; source of truth |
| node → node_version | 1—* | version immutable + signed |
| organization ↔ node_version | M:N via org_installed_node | install = grant availability |
| organization → mcp_server → mcp_tool | 1—*, 1—* | tools discovered on registration |
| execution → cost_entry | 1—* | projection of events |
| marketplace_listing → node \| template | * → 1 | polymorphic via `type` + `ref_id` |
| organization → enrichment_cache_entry | 1—* | per-org; never shared |
| organization → parent organization | *—1 | agency multi-client (§2.1) |

---

## 10. Invariants worth tattooing on the wall

1. **Every tenant-scoped row has `org_id`; cross-org references fail at load time, not just at query time.**
2. **A running execution is immune to edits** — it pins its `workflow_version` and `node_pins`.
3. **Credential plaintext exists only in worker memory at execution time.** Use ≠ read.
4. **The event log is append-only and is the source of truth;** history, live view, audit, and cost are projections.
5. **Idempotency key `(run_id, node_id, attempt)`** — retries and crash recovery never double-fire side effects.
6. **Large payloads live in object storage;** Postgres holds references.
7. **The enrichment cache is per-org;** a cache hit never crosses a tenant boundary.
8. **Node versions are immutable and signed;** publishing v2 never alters a workflow pinned to v1.
