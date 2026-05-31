"""Identity & Tenancy domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §2).

Owns: organizations, users, memberships, RBAC, sessions, api_tokens.

The highest-stakes boundary: every other domain is scoped by org_id, and tenant
isolation is enforced here (SECURITY.md §4). The "use ≠ read" credential rule is
an authorization concern that originates from this domain's RBAC model.

Phase 1 (WS-4): auth + single-org tenancy with org_id discipline from day one.
Multi-org / agency sub-orgs / full role matrix: Phase 5.
"""
