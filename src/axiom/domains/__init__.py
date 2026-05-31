"""Domains layer — the eight bounded contexts (BLUEPRINT.md §4).

Each subpackage is a domain boundary. Dependencies flow inward toward
Identity/Tenancy and the event log; nothing here depends on delivery (api/worker/
cli). The one explicit domain-to-domain rule (enforced by import-linter): the
execution engine depends on node_runtime through an interface, never the reverse.

These are intentionally empty in Phase 0. Establishing the packages now — while
they hold no code — is what lets import-linter constrain the FIRST real code
(audit R1). Entities and logic arrive in Phase 1+ per DOMAIN_MODEL.md.
"""
