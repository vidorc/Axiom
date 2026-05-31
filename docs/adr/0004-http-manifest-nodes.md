# ADR-0004: HTTP-manifest as the primary node type

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founding CTO, Principal Architect
- **Related:** `BLUEPRINT.md` §2 (principle 3), `SDK_SPEC.md`, ADR-0003, ADR-0006, `SECURITY.md` (trust tiers)

## Context

The single biggest determinant of how many community integrations get written is **how hard it is to write one.** n8n's ~1,200+ community nodes exist largely because authoring one is a small, well-scoped file. If adding an Axiom integration requires understanding our DAG engine, our async model, and a security sandbox, we will get a dozen contributors, not a thousand — and the ecosystem moat ([ADR-0006](0006-ecosystem-as-moat.md)) never forms.

Two facts shape the decision:
- **Most GTM integrations are "call this REST API with these params and map the response."** Apollo, Prospeo, Smartlead, most enrichment and outreach providers are HTTP-with-auth. They need *configuration*, not *code*.
- **Arbitrary community code is a security liability** (see `SECURITY.md`). Every code node is potential arbitrary code execution in the worker process; reviewing it is expensive and sandboxing it (in cloud) is hard.

The original brief left "what a node is made of" unspecified — the most important thing it omitted.

## Decision

The **primary, dominant node type is a declarative HTTP manifest** — a JSON/YAML descriptor (endpoint, auth type, input mapping, output mapping, pagination, rate-limit hints, cost model, UI schema) with **no executable code**. The engine interprets the manifest to make the call.

We support **three node kinds**, in priority order:
1. **HTTP-manifest (declarative, no code)** — the ~80% case and the dominant contribution path. Contributable by non-engineers. Inherently safe to run in-process (no arbitrary code).
2. **Code node (Python in year 1)** — the power path for real logic: scraping, complex transforms, AI orchestration. The escape hatch, not the default.
3. **MCP node** — an MCP server's tool surfaced as a node (see ADR-0006); covers "someone already exposed this as a tool."

## Alternatives considered

### Code-only nodes (every node is a Python/JS package) — rejected
Maximally flexible, but: it makes *every* integration a code-review + security problem, raises the contribution barrier to "can write and ship a package," and shrinks the contributor pool to engineers. It directly threatens the flywheel. Flexibility we rarely need at the cost of contributions we always need.

### Declarative-only (no code path at all) — rejected
Clean and safe, but it caps the ceiling: scraping, multi-step auth, non-trivial pagination, transform-heavy nodes, and AI orchestration cannot be expressed as pure config. Power users would hit a wall and leave. We need the escape hatch.

### A bespoke node DSL — rejected
Inventing a domain-specific language is a tax on *us* (build + maintain + document the DSL) and on *contributors* (learn a language used nowhere else). A JSON-Schema-grounded manifest reuses concepts contributors already know.

## Consequences

### Positive
- **Non-engineers can contribute integrations** by writing a manifest. This is the highest-leverage investment in the ecosystem moat.
- **Manifests are trivially reviewable and safe** — declarative, no code, in-process execution carries no arbitrary-code risk. This is *why* the trust-tier model in `SECURITY.md` can keep most nodes out of the sandbox entirely.
- **The manifest's input/output JSONSchema does triple duty:** it auto-generates the node's config form in the builder, enables design-time type-checking of edges, and documents the node — one artifact, three payoffs.
- **The cost model + rate-limit hints in the manifest** feed the cost ledger and the rate-limiter for free.

### Negative / accepted costs
- **The manifest format is now a hard contract** we must version and maintain carefully (see `SDK_SPEC.md` versioning). A breaking manifest-schema change is expensive.
- **The 20% that needs code still needs code** — and that path carries the full security weight. Accepted; that's what trust tiers are for.
- **Expressiveness pressure on the manifest.** Contributors will push the declarative format toward becoming a programming language (conditionals, loops, transforms). We must hold the line: when a node needs real logic, it becomes a *code node*. The manifest stays declarative. Resisting "just one more directive" is an ongoing discipline.

### Revisit triggers
1. A large fraction of *attempted* manifest contributions fail because the format can't express common real integrations — signals the declarative/code boundary is drawn in the wrong place.
2. The manifest format accretes so many conditional/transform directives that it has become a programming language by accident — time to formalize the code path instead.
3. Demand for a **JS/TS code-node runtime** becomes loud enough to schedule — an addition under a new ADR, complementary to this one.
