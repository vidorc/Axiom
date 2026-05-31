# ADR-0007: Apache-2.0 core with DCO; open-core for cloud

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founder/CEO, Founding CTO
- **Related:** `BLUEPRINT.md` §5, [ADR-0006](0006-ecosystem-as-moat.md)

## Context

Licensing is the textbook **expensive-to-reverse** decision, and the design review flagged it as load-bearing without resolving it. It must be locked *before* the first contributor commits, because the choice cannot be applied retroactively: you cannot collect sign-offs or agreements from people who have already contributed under different terms. The `LICENSE` file, `CONTRIBUTING.md`, and whether CI needs a contributor-agreement bot all flow from this decision.

The forces:

- **The moat is the ecosystem ([ADR-0006](0006-ecosystem-as-moat.md)).** Anything that adds friction to contribution works directly against the one thing we're betting the company on. Contribution velocity is not a nice-to-have; it is the strategy.
- **We still need commercial viability.** A cloud platform (Phase 6) is the revenue model. The license must not let a hyperscaler trivially resell our managed offering and outcompete us on our own code.
- **Contributor-agreement friction is real and measurable.** CLAs (Contributor License Agreements) are documented to reduce first-time contributions — they require a legal-feeling signing step before a first PR can merge. DCO (Developer Certificate of Origin) is a one-line `Signed-off-by` git trailer with effectively zero friction.
- **Permissive vs. copyleft.** AGPL/SSPL would deter exactly the technical and agency users we want (ADR-0005) and complicate their internal use; we rejected it in `BLUEPRINT.md` §5.

## Decision

1. **The core is licensed Apache-2.0** — the workflow engine, node runtime, node SDK, core nodes, MCP runtime, workflow builder, and CLI.
2. **Contributions are governed by the DCO**, not a CLA. Every commit carries a `Signed-off-by` trailer (`git commit -s`); CI verifies its presence. No separate agreement, no copyright assignment.
3. **Open-core for commercialization.** Cloud-specific *control-plane* components (managed hosting, billing, autoscaling orchestration, SSO/SCIM, audit-export, the hosted marketplace backend) are developed as proprietary code in a separate boundary and are **not** part of the Apache-2.0 core.
4. **The engine and SDK stay permanently permissive.** If we ever need to defend the cloud offering against verbatim resale, we may apply a **time-delayed BSL** to *cloud-specific control-plane components only* — never to the engine, runtime, or SDK.

## Alternatives considered

### Apache-2.0 core + CLA — rejected
Preserves maximum commercial optionality (a CLA grants us relicensing rights, enabling a future dual-license or relicense). Rejected because **CLA friction measurably suppresses first-time contributions**, which directly attacks the ecosystem moat. We are explicitly trading away relicensing optionality to maximize contribution velocity — a deliberate bet that *the ecosystem is worth more than the option to relicense*. Consistent with ADR-0006.

### Pure Apache-2.0, including the cloud platform — rejected
Maximal openness and community trust, but it leaves **no licensing backstop** against a hyperscaler reselling our managed cloud verbatim. We'd rely entirely on the ecosystem + data-gravity moat with nothing in reserve. Given the cloud is the revenue model, retaining a *narrow, optional* BSL lever on cloud-only control-plane code is prudent insurance — at no cost to the open core.

### AGPL / SSPL core — rejected (already, in `BLUEPRINT.md` §5)
Strong copyleft defense, but it scares off the technical/agency users who are our wedge and complicates their adoption and contribution. The deterrent to adoption outweighs the protection.

## Consequences

### Positive
- **Lowest-friction contribution path** (DCO one-liner) — maximizes the contributor flywheel the moat depends on.
- **Maximum adoption** from a permissive, well-understood license enterprises and agencies can use without legal review.
- **A commercial path that doesn't poison the open core** — open-core keeps the engine/SDK fully free while protecting the revenue surface.
- **A defensive option held in reserve** (time-delayed BSL on cloud control-plane only) without paying for it now.

### Negative / accepted costs
- **We forgo relicensing rights over the core.** Without a CLA we don't hold contributor copyright, so we *cannot* later relicense the Apache-2.0 core. This is the deliberate price of low contribution friction, and we accept it permanently for the engine/runtime/SDK.
- **Fork risk is real.** Apache-2.0 permits closed-source forks. Our defense is execution speed + the ecosystem/data-gravity moat (ADR-0006), not the license.
- **Open-core boundary discipline is now an ongoing cost.** We must keep cloud-specific control-plane code cleanly separated from the Apache core, or the boundary erodes and the commercial protection with it. This is an architectural constraint, not just a legal one.
- **DCO requires CI enforcement** and contributor education (every commit `-s`); a missing sign-off blocks a merge until amended.

### Revisit triggers
1. A hyperscaler or competitor **resells our managed cloud verbatim** and materially threatens revenue → evaluate applying the time-delayed BSL to cloud control-plane components (a new ADR; the core stays Apache).
2. Contribution volume is being **bottlenecked by something other than license friction**, and a CLA's optionality would unlock a needed business move → reconsider, knowing the contribution-velocity cost.
3. The open-core boundary proves **unmaintainable in practice** (cloud and core code keep entangling) → revisit the commercialization structure.
