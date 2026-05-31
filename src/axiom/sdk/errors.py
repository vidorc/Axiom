"""Node error taxonomy (SDK_SPEC.md §4.3).

A node communicates failure via a typed error *class*; the engine decides
retry-vs-terminal from the class, NOT from the node guessing. This is why retry
is engine policy, not a per-node method (SDK_SPEC.md §4.2). Defined in the SDK
because it is part of the public node contract.

The engine reads `retryable` to drive backoff; HTTP-manifest nodes map provider
responses to these classes via their manifest `errors` block, and code nodes
raise these directly.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorClass(StrEnum):
    """The fixed taxonomy of node failure classes (SDK_SPEC.md §4.3)."""

    RATE_LIMITED = "rate_limited"  # retryable: 429 / throttle (honor Retry-After)
    PROVIDER_ERROR = "provider_error"  # retryable: 5xx / transient upstream
    TIMEOUT = "timeout"  # retryable: network / timeout
    INVALID_INPUT = "invalid_input"  # terminal: 4xx the input can't satisfy
    AUTH_ERROR = "auth_error"  # terminal: bad/expired credential
    NOT_FOUND = "not_found"  # terminal: resource absent
    INTERNAL = "internal"  # terminal: bug in the node


# Which classes the engine will retry. Single source of truth for the
# retryable/terminal split (SDK_SPEC.md §4.3).
RETRYABLE_CLASSES: frozenset[ErrorClass] = frozenset(
    {ErrorClass.RATE_LIMITED, ErrorClass.PROVIDER_ERROR, ErrorClass.TIMEOUT}
)


class NodeError(Exception):
    """Raised by a node's execute() to signal typed failure.

    The engine inspects `error_class` (and RETRYABLE_CLASSES) to decide whether
    to retry with backoff or fail the node terminally.
    """

    def __init__(
        self,
        error_class: ErrorClass,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        # Populated for RATE_LIMITED when the provider sends Retry-After.
        self.retry_after_seconds = retry_after_seconds

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE_CLASSES


class ValidationError(NodeError):
    """Raised by a node's validate() — terminal, and crucially no external call
    has been made yet, so no spend occurs (SDK_SPEC.md §4.1)."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorClass.INVALID_INPUT, message)
