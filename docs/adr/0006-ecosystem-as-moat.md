# ADR-0006: Ecosystem + data gravity as the moat (not MCP/BYOK)

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founding CTO, Founder/CEO
- **Related:** `BLUEPRINT.md` §1.4, §1.5, ADR-0004, ADR-0005

## Context

We must be clear-eyed about what is actually defensible, because the moat thesis drives where we spend scarce engineering time. The original brief asserted that **MCP support** and **BYOK** are the moat. Under scrutiny, neither is:

- **MCP is an open protocol.** Anyone can implement an MCP client in an afternoon; major agent frameworks already have. Supporting MCP is *table stakes for technical credibility*, not a defensible advantage. "MCP is our moat" is "HTTP is our moat."
- **BYOK is copyable in a weekend.** It's a billing and key-management choice, not a technology. A well-funded incumbent (Clay, Apollo, n8n) could ship a "BYOK mode" and erase that single differentiator.
- **Open source is distribution, not defensibility.** Anyone can fork the code. OSS wins *adoption*; it does not, by itself, retain.

A wrong moat thesis would have us over-invest in MCP breadth and BYOK polish while under-investing in the things that actually compound.

## Decision

The moat is the **compounding flywheel of (a) ecosystem depth, (b) data gravity, and (c) standard-setting** — none of which exist on day one, all of which strengthen with usage. **MCP and BYOK are adoption *accelerants*, not the moat.** We invest engineering time accordingly: node/SDK ergonomics and the data-gravity features are P0; MCP breadth is valuable leverage but not where defensibility is built.

```
   more users ──▶ more node + template contributions ──▶ Axiom does more
       ▲                                                        │
       └────────────────── more reasons to adopt ◀──────────────┘
```

## The three compounding assets

### (a) Ecosystem depth
A curated, deep library of nodes and templates that is genuinely hard to replicate because it's the output of a *community over time*, not a feature. The enabling investment is **node-authoring ergonomics** — the HTTP-manifest path ([ADR-0004](0004-http-manifest-nodes.md)) so non-engineers contribute, plus a registry with attribution and install counts so contributors get reputation and distribution (their two strongest motivations).

### (b) Data gravity
Execution history, cost analytics, and especially the **enrichment cache** accrue value the longer a customer runs and raise switching cost over time. This deliberately re-introduces the stickiness that pure BYOK removes (`BLUEPRINT.md` §1.5). The enrichment cache is the sharpest instance: it's simultaneously a *cost-saver* (don't re-pay Apollo for last week's domain — feeds the ADR-0005 wedge) and a *lock-in* (the cache lives in Axiom).

### (c) Standard-setting
If the Axiom node SDK becomes *the* way technical GTM people build and share integrations, the format itself becomes a standard with switching cost. This is the slowest to form and the strongest if it does.

## Alternatives considered (moat theses we reject)

- **"MCP is the moat."** Rejected: open protocol, zero defensibility. We still build a first-class MCP runtime — for *leverage* (it collapses our integration backlog: any tool with an MCP server works in Axiom for free), not for defense.
- **"BYOK is the moat."** Rejected: a billing choice, weekend-copyable. It's a superb *wedge* (ADR-0005), not a moat.
- **"Open source is the moat."** Rejected: forkable. OSS is our distribution and contribution engine, not our retention.
- **"The canvas/builder is the moat."** Rejected: UI is copyable and table-stakes; a great canvas with thin nodes loses to the reverse.

## Consequences

### Positive
- **Engineering priorities are correctly ordered:** SDK ergonomics, the registry/marketplace, and data-gravity features (history, cost ledger, enrichment cache) are P0. We don't burn the year chasing MCP breadth as if it were defensible.
- **Honest external messaging.** We sell MCP/BYOK as benefits, not as a moat we'll be caught not having.
- **Reinforces ADR-0004 and ADR-0005:** the manifest node path *is* the ecosystem investment; agencies *are* the contribution/distribution multiplier.

### Negative / accepted costs
- **The moat is slow.** Flywheels don't spin on day one; early on we are genuinely under-defended and must win on wedge + execution speed before an incumbent copies the features. This is the central strategic risk and we accept it consciously.
- **Ecosystem quality is a permanent operating cost.** A marketplace full of broken nodes is worse than none. We must budget curation, verification, and fast maintainership indefinitely — a flywheel that stalls (rotting PRs, broken nodes) actively repels contributors.
- **Data-gravity features must be built deliberately**, not assumed; they're easy to under-prioritize against shinier work.

### Revisit triggers
1. The flywheel **fails to spin** — contribution volume stays flat despite good SDK ergonomics — forcing a rethink of what actually retains users.
2. An incumbent ships BYOK + MCP and our differentiation collapses **faster than the flywheel forms** — a signal to compete on a different axis or accelerate the data-gravity lock-in.
3. A genuinely defensible technical asset emerges (e.g. a proprietary data layer or a uniquely hard execution capability) that should be added to — or reorder — the moat thesis.
