# Web (frontend)

## Marketing surface — `index.html`

A self-contained static landing page (`index.html` + `styles.css`), built as a
faithful 1:1 implementation of the locked design-token system in
[`DESIGN.md`](../DESIGN.md): the ink-and-gray Vercel-derived language, the
multi-stop mesh gradient as the only decoration, Geist/Geist-Mono type (via the
documented Inter + JetBrains Mono substitutes), the stacked-shadow elevation
ladder, pill CTAs, and the dark-band polarity flip.

No build step, no framework, no tooling — just open it:

```sh
# from the repo root
open web/index.html          # macOS
xdg-open web/index.html      # Linux
# or serve it:
python -m http.server -d web 8080   # → http://localhost:8080
```

Keeping it framework-free is deliberate: it gives us a real marketing page now
without scaffolding Next.js/turborepo before Phase 3 (pre-build audit R4).

## The Phase 3 app — still a placeholder

The **product** UI — the workflow builder, run viewer, and marketplace
([`UI_SYSTEM.md`](../UI_SYSTEM.md)) — remains Phase 3 and is **not** scaffolded
here. When Phase 3 begins, this directory becomes the Next.js + TypeScript +
Tailwind + shadcn/ui + React Flow app described in `UI_SYSTEM.md`, built on the
same `DESIGN.md` tokens and consuming the same API the CLI uses (PHASE_1.md §7.5).

Until then, the backend (`src/axiom/`) plus this static page are the whole
surface area. The product itself is the engine.
