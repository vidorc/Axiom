"""Axiom Node SDK — the public node contract (SDK_SPEC.md).

This package is deliberately INDEPENDENT: it imports nothing from axiom.domains,
axiom.platform, or the delivery layer (enforced by import-linter Contract 3). The
reason is forward-looking — the SDK is the one component we expect to extract as a
standalone distributable in Phase 2 (`pip install axiom-sdk`) so node authors
don't pull in the whole monolith. Keeping it independent now makes that a
mechanical lift later (ADR-0006: node-authoring ergonomics are the moat).

The public surface, re-exported here so authors write ``from axiom.sdk import
BaseNode, NodeError``:

  * ``BaseNode``            — the class a code node subclasses (node.py)
  * ``ExecutionContext``    — the platform surface handed to execute() (context.py)
  * ``NodeError`` / ``ErrorClass`` / ``ValidationError`` — the error taxonomy
                              that drives engine retry policy (errors.py)
  * ``ValidationResult`` / ``CompensationResult`` — lifecycle return types
  * ``JsonObject`` / ``JsonValue`` — the JSON-compatible IO value types
"""

from __future__ import annotations

from axiom.sdk.context import (
    CancelToken,
    CostReporter,
    CredentialAccessor,
    EnrichmentCache,
    ExecutionContext,
    JsonObject,
    JsonValue,
    RunInfo,
    StructuredLogger,
)
from axiom.sdk.errors import (
    RETRYABLE_CLASSES,
    ErrorClass,
    NodeError,
    ValidationError,
)
from axiom.sdk.node import BaseNode
from axiom.sdk.results import CompensationResult, ValidationResult

__all__ = [
    "RETRYABLE_CLASSES",
    "BaseNode",
    "CancelToken",
    "CompensationResult",
    "CostReporter",
    "CredentialAccessor",
    "EnrichmentCache",
    "ErrorClass",
    "ExecutionContext",
    "JsonObject",
    "JsonValue",
    "NodeError",
    "RunInfo",
    "StructuredLogger",
    "ValidationError",
    "ValidationResult",
]
