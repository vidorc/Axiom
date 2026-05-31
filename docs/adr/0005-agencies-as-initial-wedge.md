# ADR-0005: Agencies + Clay-cost refugees as the initial wedge

- **Status:** Accepted
- **Date:** 2026-05-31
- **Deciders:** Founding CTO, Founder/CEO
- **Related:** `BLUEPRINT.md` §1.3, ADR-0006

## Context

"The orchestration layer for GTM engineers" is the *vision*, not a go-to-market wedge. A small team cannot enter a market by addressing everyone; it enters through a narrow segment that (a) feels a sharp, quantifiable pain, (b) is reachable without a large sales motion, and (c) self-selects for our product's constraints. The deepest such constraint is **BYOK** — to get value, a user must bring their own provider keys, which only technical, motivated users will do.

Two candidate segments emerged from the product review, and they switch for *different* reasons:
- **Clay-cost refugees:** Clay's action-based pricing becomes punitive at volume. They want to pay providers directly and stop being metered.
- **n8n/Make refugees:** want GTM-shaped primitives the generic tools lack. (A real audience, but they switch for *features*, which we won't have depth in early — a weaker early fit.)

## Decision

We enter through **two reinforcing beachheads: Clay-cost refugees and growth agencies.** We lead messaging with the **Clay-cost story** (it's concrete and self-selecting) and treat **agencies as the highest-value early customer** (technical, high-volume, cost-sensitive, and a distribution channel). We do **not** market to the broad "GTM engineer platform" early, and we de-prioritize the n8n-feature-refugee until node depth exists.

## Why these two, specifically

### Clay-cost refugees — the messaging wedge
- **Quantifiable value.** "Bring your own keys, pay your provider directly, we never meter you" is a number on a savings calculator, not a vibe. A team running 50k enrichments/month against metered pricing has a spreadsheet that sells the switch for us.
- **Self-selecting for BYOK.** Anyone motivated by this pain is, by definition, willing and able to run their own keys — which is exactly the user our architecture requires. The wedge filters *for* our constraint instead of against it.

### Agencies — the highest-value early customer
- **Technical + high-volume + cost-sensitive.** They run more volume than individual teams and feel metered pricing most acutely.
- **They resell.** An agency that builds client delivery on Axiom becomes a power user, a source of templates/nodes, *and* a distribution channel to every client they serve. One agency win propagates.
- **They need structure we can provide cheaply:** multi-client org separation, per-client templates/white-labeling, and the cost story to pitch *their* clients.

## Alternatives considered

### Lead with "GTM engineers" broadly — rejected
Too diffuse for a small team. No single sharp pain to anchor messaging, no obvious channel, and it doesn't filter for BYOK-willingness. A vision, not a wedge.

### Lead with n8n/Make feature-refugees — rejected for *early* phase
They switch for GTM-primitive depth we won't have until the node ecosystem matures (Phase 2+). Pursuing them before node depth exists means losing on our weakest axis. Revisit once depth exists.

### Enterprise RevOps first — rejected
Long sales cycles, security/compliance gates, and SSO/SAML demands that a pre-PMF OSS team can't satisfy. Wrong motion for this stage; a later expansion, not an entry.

## Consequences

### Positive
- **A concrete, copy-ready value proposition** (cost savings) that converts without a sales team.
- **The wedge self-selects for technical, BYOK-capable users** — minimizing the onboarding-wall problem within the target segment.
- **Agencies multiply reach and seed the ecosystem** (templates, nodes, referrals) — directly feeding the moat in ADR-0006.
- **Roadmap alignment:** justifies prioritizing multi-client org structure, the enrichment cache (cost story), and templates earlier than a generic roadmap would.

### Negative / accepted costs
- **We under-serve the n8n-feature-refugee early** and may cede some of that audience temporarily. Accepted — we can't win on feature depth before we have it.
- **Agencies demand multi-client structure and reliability sooner** than a single-team product would. This pulls some Phase-5 work earlier; we accept the reprioritization because the segment is worth it.
- **Cost-driven buyers are price-sensitive and lower-switching-cost by nature** — exactly the retention risk in `BLUEPRINT.md` §1.5. Mitigated by deliberately engineering data-gravity stickiness (history, enrichment cache, ecosystem).
- **The onboarding wall still bites** anyone outside the self-selected segment. Mitigated by keyless/free first-run nodes.

### Revisit triggers
1. The Clay-cost narrative fails to convert in practice (the savings math doesn't move technical buyers) — re-examine the wedge.
2. Node depth matures enough that the **n8n-feature-refugee** becomes a stronger, larger second wedge — expand messaging deliberately.
3. Inbound pull from **enterprise RevOps** with budget appears organically — a signal to consider an up-market motion, under a new ADR.
