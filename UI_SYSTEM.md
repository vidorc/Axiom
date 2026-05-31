# Axiom — UI System & Experience Design

**Status:** Committed (post-review)
**Last updated:** 2026-05-31
**Audience:** Frontend engineers and designers building the Axiom web app (Phase 3).

> **The visual language is already locked.** `DESIGN.md` is the canonical design-token system (a Vercel-derived language: ink + gray, the mesh gradient as the only decoration, Geist/Inter type, stacked shadows, pill CTAs, dark-band polarity flips). **This document does not redefine colors, type, or components — it defines *structure and behavior*: information architecture, navigation, the workflow builder, and the interaction principles that make Axiom feel like a premium developer tool.** Where a token is needed, it references `DESIGN.md` (e.g. `{colors.primary}`), never a new value.

The stack is locked too (`BLUEPRINT.md` §6): **Next.js + TypeScript + Tailwind + shadcn/ui + React Flow.**

---

## 1. Design philosophy

Three principles, in tension-resolution order. When two layouts are otherwise equal, the earlier principle wins.

### 1.1 The engine is the product; the canvas is a window into it
The original brief over-indexed on "a world-class canvas, better than n8n." **A beautiful canvas with shallow nodes loses to a plain canvas with deep ones** (`BLUEPRINT.md` §1.6, top failure mode). So we spend the UI budget on **legibility of execution** — what ran, what it cost, what it returned — far more than on pan/zoom polish. The canvas must be *good*; the run-viewer and output table must be *exceptional*.

### 1.2 The data is the hero
GTM users live in **rows** — companies, leads, enriched records. This is what Clay gets right and generic automation tools get wrong. **The single most important surface in Axiom is a fast, dense, spreadsheet-like output table.** If we build one great component, it's the table, not the graph.

### 1.3 Keyboard-first, command-palette-everywhere
Per the brief and the Linear/Raycast/Arc lineage. `⌘K` adds nodes, navigates, runs, searches — *everything*. This is both a premium-feel differentiator versus n8n's mouse-heavy UX **and** a self-selection mechanism: it appeals to exactly the technical users our wedge targets ([ADR-0005](docs/adr/0005-agencies-as-initial-wedge.md)).

### 1.4 What we explicitly avoid
- **Enterprise dashboard sprawl.** No 14-widget home screen. The home is "your workflows" + "recent runs," nothing else.
- **Cluttered density-for-its-own-sake.** Dense *where data lives* (the table), generous whitespace everywhere else — the `DESIGN.md` "engineered" rhythm (large gaps + tight interiors).
- **Decoration beyond the system.** The mesh gradient is the only decorative chrome, at hero/marketing scale only. In-product surfaces are ink + gray.

---

## 2. The four inspirations, made concrete

The brief names Linear, Vercel, Raycast, Arc. Here is what we actually take from each — behavior, not vibes:

| Source | What we take | Where it shows up |
|---|---|---|
| **Linear** | Keyboard-first navigation, instant transitions, the collapsible left sidebar, command menu, "speed is a feature" responsiveness, opinionated minimal IA | Global nav, `⌘K`, list views, the feel of *fast* |
| **Vercel** | The entire visual language (`DESIGN.md`) — ink/gray restraint, mono for technical labels, stacked shadows, the dark-band polarity flip, the deployment-dashboard posture | Every surface's chrome; the run viewer's "deployment log" feel |
| **Raycast** | The command palette as the *primary* interface — actions, search, and creation all funnel through `⌘K`; extensions/nodes browsable from it | `⌘K` does everything; node search lives there too |
| **Arc** | Spatial calm, the sense that the tool gets out of the way; smooth, low-chrome navigation; the canvas as a place you *inhabit* | The builder canvas; transitions; the uncluttered shell |

The throughline: **a tool that respects the user's speed and stays out of the way.** Axiom should feel like `vercel deploy` looks — calm, technical, fast, confident.

---

## 3. Information architecture

A deliberately small, flat IA. Everything reachable in one or two keystrokes from `⌘K`.

```
┌── ⌘K command palette (global, the spine of navigation) ──────────────┐
│                                                                       │
│  Sidebar (collapsible, Linear-style — icon-only when collapsed)       │
│   ◆ Workflows      → list · search · folders                          │
│   ◷ Runs           → execution history · filters · cost               │
│   ⬡ Nodes          → installed nodes + library/marketplace            │
│   🔑 Credentials    → the vault (masked: label + last4 only)          │
│   ▤ Data           → enrichment caches · datasets                     │
│   📊 Observability  → cost · failure rate · provider usage            │
│   ⚙ Settings       → org · team/RBAC · billing (cloud)                │
│                                                                       │
│  Main area: context-dependent (list view, builder, or detail)         │
└───────────────────────────────────────────────────────────────────────┘
```

### 3.1 Why this IA (and not more)
- **Seven destinations, flat.** No nested mega-menus. Each maps to a domain boundary from `DOMAIN_MODEL.md`, so the IA mirrors the architecture — a user's mental model and the system's model match.
- **Workflows and Runs are separate top-level items**, reflecting the authoring/execution split (`DOMAIN_MODEL.md` §3). You design in Workflows; you observe in Runs.
- **Data is its own destination** because the data *is* the hero (§1.2) — the enrichment cache and datasets are first-class, not buried.
- **Observability is one focused destination**, not a sprawl: the dashboard shows exactly **workflow runs · API cost · failure rate · runtime metrics · provider usage** (the brief's list) and nothing more.

### 3.2 The home screen
On sign-in: a calm two-section view — **your workflows** (with last-run status chips) and **recent runs** (with status/cost). No widgets, no charts, no "welcome" clutter. The fastest path to "run my workflow" or "what happened in my last run."

---

## 4. The workflow builder (the centerpiece)

The builder is where authoring happens, built on **React Flow**. Layout:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  ‹ Workflow name ▾    [v3 ▾]                       ▶ Run    ⌘K    ◷ Runs    ⚙     │  ← top bar
├──────────────┬──────────────────────────────────────────────────┬─────────────────┤
│ NODE LIBRARY │                                                  │ INSPECTOR        │
│ (left panel) │                INFINITE CANVAS                   │ (right panel)    │
│              │                                                  │                  │
│ search ⌕     │     ┌─────────┐                                  │ • selected node: │
│ ─────────    │     │Find Cos │                                  │   config form    │
│ Scraping     │     └────┬────┘                                  │   (auto-built    │
│ Enrichment   │          ▼                                       │    from input    │
│ AI           │     ┌─────────┐                                  │    JSONSchema)   │
│ Outreach     │     │  Split  │                                  │                  │
│ Database     │     └──┬───┬──┘                                  │ • OR if a run is │
│ HTTP         │     ┌──▼─┐ ┌▼────┐                               │   selected:      │
│ MCP          │     │Enr.│ │Scrp.│   ← status glow when running  │   that node's    │
│ Custom       │     └──┬─┘ └─┬───┘                               │   live output    │
│              │     ┌──▼─────▼──┐                                │   + logs + cost  │
│ drag → canvas│     │Personalize│                                │                  │
│ or ⌘K to add │     └─────┬─────┘                                │                  │
│              │     ┌──────▼─────┐                               │                  │
│              │     │ Send Email │  ◷ 1.2s  ◈ $0.03  ▦ 240 rows  │                  │
│              │     └────────────┘  ← status · duration · cost · count chips        │
├──────────────┴──────────────────────────────────────────────────┴─────────────────┤
│  BOTTOM DOCK (tabbed, collapsible — THE GTM DIFFERENTIATOR)                          │
│  [ ▦ Output Table ]  [ ☰ Logs ]  [ ◷ Timeline ]  [ ⚠ Errors ]  [ ◈ Cost ]            │
│  ┌─ dense · virtualized · spreadsheet-like result grid ───────────────────────────┐ │
│  │ company         │ domain         │ email             │ score │ status           │ │
│  │ Acme Corp       │ acme.com       │ jane@acme.com     │  87   │ ✓ enriched       │ │
│  │ Globex          │ globex.io      │ —                 │  —    │ ⚠ no match       │ │
│  └─────────────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 The four regions

**Node library (left)** — searchable, category-tabbed (the seven `DOMAIN_MODEL.md` categories + MCP). Each entry shows a **verified badge** and **install count** (the marketplace trust signals from [ADR-0006](docs/adr/0006-ecosystem-as-moat.md)). Drag-to-canvas *or* `⌘K → "add Apollo node"`. Collapsible.

**Canvas (center)** — React Flow infinite canvas with the brief's full capability set: drag-drop, zoom, pan, node grouping, multi-select, undo/redo, keyboard shortcuts. Nodes are rendered in the `DESIGN.md` chrome (`card-marketing`-style, stacked-shadow elevation, `{rounded.md}`). **The differentiating detail: each node chip shows live `status · duration · cost · output-count` chips**, and running nodes get a **status glow** fed by the WebSocket event stream (`BLUEPRINT.md` §3.3). Execution is *visible on the graph*, not hidden in a log.

**Inspector (right)** — context-sensitive. When a node is selected at design time: its **config form, auto-generated from the node's input JSONSchema** (`SDK_SPEC.md` §3.2 — no per-node UI code ever). When a *run* is being viewed: that node's **live output, logs, and cost**. One panel, two modes, mirroring the authoring/execution duality.

**Bottom dock (the GTM differentiator)** — tabbed and collapsible: **Output Table · Logs · Timeline · Errors · Cost**. The Output Table is the hero (§1.2): a **dense, virtualized, spreadsheet-like grid** of results — the surface where GTM users actually live. This is the single component most worth over-investing in.

### 4.2 Design-time vs run-time, in one builder
The builder is the same surface for *designing* and *watching*. Hit **▶ Run** and the static graph animates: nodes light up as they're claimed, chips populate with duration/cost/counts, the output table streams rows, the timeline fills. No context switch between "edit mode" and "watch mode" — Arc-like spatial continuity. This is only possible because execution is an event stream the UI subscribes to (`BLUEPRINT.md` §3.3).

### 4.3 Version awareness
The top bar's **`[v3 ▾]`** exposes the workflow-version model (`DOMAIN_MODEL.md` §3). Switching versions is non-destructive; running a workflow pins its version. When a node has a newer version available, an unobtrusive indicator offers the assisted upgrade (`SDK_SPEC.md` §6.3) — with the I/O-diff surfaced so the user upgrades with eyes open.

---

## 5. The execution / run viewer

Reached from **Runs** (history) or by clicking ▶ in the builder. It is the builder's canvas **replayed from the event log** — the same graph, with per-node timing, the output table, logs, errors, and cost. Because everything is a projection of `execution_event` (`DOMAIN_MODEL.md` §4), the run viewer is "the builder in read-only replay mode," not a separate UI to build.

- **Live runs** stream over WebSocket (status glow, rows filling in real time).
- **Historical runs** replay the same view from the stored event log — time-travel debugging falls out for free.
- The viewer's aesthetic deliberately evokes a **Vercel deployment log**: calm, mono-labeled, technical, scannable.

This is where the event-sourcing decision pays off *visually* — three "features" (history, live view, debugging) are one surface.

---

## 6. The command palette (`⌘K`)

The Raycast-inspired spine of the whole app. It is not a search box bolted on; it is the **primary way to do anything**:

- **Navigate** — "go to Runs," "open workflow Cold Outbound."
- **Create** — "new workflow," "add Apollo node," "add credential."
- **Act** — "run this workflow," "cancel run," "duplicate node."
- **Search** — across workflows, runs, nodes, the marketplace.
- **Browse nodes** — node search lives here too, so adding a node never requires reaching for the mouse.

Every primary action in the app has a keyboard shortcut *and* a `⌘K` entry. The palette is the reason a power user never touches the mouse — and the reason Axiom feels fast.

---

## 7. Node library, marketplace & templates

- **Node library** (in-builder, left panel) and the **Marketplace** (a destination under Nodes) share the same node cards: icon, label, category, **verified badge**, **install count**, author attribution.
- **Templates are first-class and arguably more important than individual nodes for activation** — a new user wants a *working workflow*, not a parts bin (`DOMAIN_MODEL.md` §7). The marketplace surfaces "Cold Outbound + Enrich + Personalize" as an installable template that drops a complete graph onto the canvas (credentials stripped — `SECURITY.md`, the user supplies their own BYOK keys).
- In year 1 the marketplace is **a page in the app**, not a separate web property (`BLUEPRINT.md` §8, Phase 5). The social/hosted marketplace comes later; the in-app browse/install experience comes first.

---

## 8. Onboarding (designing around the BYOK wall)

The hardest UX problem isn't the canvas — it's the **cold-start wall** (`BLUEPRINT.md` §1.6): BYOK means a user must bring keys before seeing value, while Clay lets you enrich a row on signup. The UI must fight this:

- **First-run runs with zero keys.** Surface **keyless/free nodes** (HTTP, fetch a public page, an AI node via a trial key, free-tier providers) so a user builds and runs *something real* in under 5 minutes — before being asked for a single API key.
- **Just-in-time credential prompts.** Don't front-load "paste 6 keys." Ask for a provider key *at the moment* a node needs it, in context, with a link to where to get it.
- **A starter template on first load**, pre-wired with keyless nodes, that the user can run immediately to feel the engine work.

The activation metric the UI optimizes for: **time-to-first-successful-run**, target < 5 minutes with no keys configured.

---

## 9. Responsiveness, accessibility, dark mode

- **Speed is a feature (Linear).** Instant view transitions, optimistic UI, virtualized lists/tables, no spinner where a skeleton or cached state will do. Perceived latency is a first-class design constraint.
- **Dark mode is first-class**, per the brief — built on the `DESIGN.md` polarity-flip system (`{colors.primary}` surfaces, `{colors.on-primary}` text), not a hacked-in inversion.
- **Accessibility is non-negotiable:** keyboard-navigable everything (we're keyboard-first anyway, which helps), visible focus states, WCAG AA contrast (the `DESIGN.md` ink/gray scale already targets this), ARIA for the canvas and table, respect for `prefers-reduced-motion` (the status-glow and transitions must degrade gracefully). *Full WCAG conformance requires manual assistive-tech testing and expert review — the design system enables it but does not by itself certify it.*

---

## 10. What Phase 3 builds first (priority order)

When the builder phase begins (`BLUEPRINT.md` §8, Phase 3), build in this order — derived directly from the principles above:

1. **The app shell** — sidebar, `⌘K`, navigation, the `DESIGN.md` system applied. (Everything hangs off this.)
2. **The output table** — the hero component (§1.2). Dense, virtualized, fast. Over-invest here.
3. **The run viewer** — live + historical, over the WebSocket event stream. (Proves the engine is legible.)
4. **The canvas** — React Flow with nodes, edges, status chips, the status glow.
5. **The inspector** — JSONSchema-driven config forms + run-output mode.
6. **The node library + onboarding** — keyless first-run, just-in-time credential prompts.

Note the order **inverts the brief's instinct**: the table and run viewer come *before* canvas polish, because legibility of execution beats canvas beauty (§1.1). A user who can *see what their workflow did* will forgive a plain canvas; a user with a gorgeous canvas and an opaque run will not stay.

---

## 11. Where this doc defers to others

- **Visual tokens** (color, type, spacing, shadows, component chrome) → `DESIGN.md`, always.
- **What a node's config form contains** → the node's input JSONSchema (`SDK_SPEC.md` §3.2).
- **What the run viewer can show** → the `execution_event` projection (`DOMAIN_MODEL.md` §4).
- **What's masked in the credentials UI** → `SECURITY.md` (label + last4 only; no reveal).
- **When each surface ships** → `BLUEPRINT.md` §8 roadmap.

This document owns *structure and behavior*. It does not own pixels (that's `DESIGN.md`) or data (that's `DOMAIN_MODEL.md`).
