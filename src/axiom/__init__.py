"""Axiom — the orchestration layer for GTM engineers.

This is a single installable package run in multiple modes (api / worker / cli),
NOT multiple services. See docs/adr/0002-modular-monolith.md and BLUEPRINT.md §3.1.

Package layers (dependencies flow downward only; enforced by import-linter in
pyproject.toml under [tool.importlinter]):

    axiom.api | axiom.worker | axiom.cli     (delivery — thin entrypoints)
        depends on ▼
    axiom.domains                            (the 8 domain boundaries, BLUEPRINT.md §4)
        depends on ▼
    axiom.platform                           (infra: config, logging, db, redis)
        depends on ▼
    axiom.shared                             (base types + errors; depends on nothing)

    axiom.sdk                                (independent; extractable in Phase 2)
"""

__version__ = "0.0.0"
