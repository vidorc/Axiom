"""Credential Vault domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §5, SECURITY.md §5).

Owns: encrypted provider keys and MCP auth secrets.

Secrets have a wholly different lifecycle (envelope encryption, rotation,
never-log) than business data, so they are isolated to bound the blast radius of
any bug. Invariants this domain guarantees: plaintext exists only in worker
memory at execution time; no API path returns plaintext ("use ≠ read"); a node
receives only the credentials its manifest declares.

Phase 1 (WS-2): production envelope encryption + the no-leak guarantee.

Public surface:
  * ``CredentialVault`` — persistence + envelope crypto; the only component that
    touches plaintext, on two paths (bind/rotate in, resolve out-to-engine).
  * ``CredentialView`` — the masked, safe-to-return shape (label + last4 + meta,
    never the secret).
  * ``CredentialNotFoundError`` — absent or cross-org (indistinguishable).
"""

from __future__ import annotations

from axiom.domains.credentials.store import (
    CredentialNotFoundError,
    CredentialVault,
    CredentialView,
)

__all__ = [
    "CredentialNotFoundError",
    "CredentialVault",
    "CredentialView",
]
