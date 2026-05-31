"""Node result value objects (SDK_SPEC.md §4.1, §4.5).

Small, immutable return types for the two non-`execute` lifecycle methods.
`execute()` returns the node's output object directly (a `JsonObject`) or raises
a typed `NodeError` — it does not wrap success in an envelope, because the engine
already knows a returned value means success and a raised `NodeError` means
failure. `validate()` and `compensate()`, by contrast, have a richer "ok / not
ok / not applicable" shape that benefits from an explicit result type.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axiom.sdk.context import JsonValue


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The outcome of a node's pure `validate()` pass.

    `validate()` runs *before* any external call, so a failure here costs
    nothing (SDK_SPEC.md §4.1). `errors` collects every problem found so the UI
    can surface them all at once rather than one-at-a-time.
    """

    ok: bool
    errors: tuple[str, ...] = ()

    @classmethod
    def success(cls) -> ValidationResult:
        return cls(ok=True)

    @classmethod
    def failure(cls, *errors: str) -> ValidationResult:
        # A failure with no message is a programming error — be loud about it.
        if not errors:
            errors = ("validation failed",)
        return cls(ok=False, errors=errors)


@dataclass(frozen=True, slots=True)
class CompensationResult:
    """The outcome of a best-effort `compensate()` (SDK_SPEC.md §4.5).

    Compensation is opt-in and never a transactional guarantee. The three
    states map directly to what the engine records on the compensating event:
    ``unsupported`` (the node can't undo its effect — the default),
    ``compensated`` (the effect was reversed), and a failure (the attempt ran
    but could not complete).
    """

    status: str  # one of: "compensated", "unsupported", "failed"
    detail: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    @classmethod
    def compensated(cls, detail: str | None = None) -> CompensationResult:
        return cls(status="compensated", detail=detail)

    @classmethod
    def unsupported(cls) -> CompensationResult:
        """The default: most GTM side effects (a sent email) cannot be undone."""
        return cls(status="unsupported", detail="node does not support compensation")

    @classmethod
    def failed(cls, detail: str) -> CompensationResult:
        return cls(status="failed", detail=detail)
