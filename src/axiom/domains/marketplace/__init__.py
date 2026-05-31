"""Marketplace domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §7, ADR-0006).

Owns: node/template listings, install counts, attribution — plus the data-gravity
surfaces (templates, the per-org enrichment cache).

The flywheel surface and the moat (ADR-0006): read-mostly, abuse-prone,
community-facing — a different operational profile from the execution core. The
enrichment cache is both a cost-saver (the wedge) and a lock-in (the moat), and
is strictly per-org — a cache hit never crosses a tenant boundary.

Deferred to Phase 5. The package exists now only to reserve the boundary.
"""
