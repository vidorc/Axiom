"""Base exception hierarchy.

Domain-specific errors subclass these. The node-execution error *taxonomy*
(retryable vs terminal) lives in the SDK contract (axiom.sdk), not here, because
it is part of the public node contract — see SDK_SPEC.md §4.3.
"""

from __future__ import annotations


class AxiomError(Exception):
    """Root of all Axiom errors. Catch this to catch anything we raise."""


class ConfigError(AxiomError):
    """Invalid or unsafe configuration. Raised at startup, before serving."""


class NotFoundError(AxiomError):
    """A requested entity does not exist (within the caller's tenant scope)."""


class TenantIsolationError(AxiomError):
    """A reference crossed an org boundary. Always a bug or an attack.

    Raised when an entity reference resolves outside the caller's org_id. See
    SECURITY.md §4.3 (reference-integrity at load time) — this must fail loud,
    never be silently ignored.
    """
