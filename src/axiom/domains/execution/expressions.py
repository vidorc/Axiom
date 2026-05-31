"""Edge-mapping expression layer — re-exported from ``axiom.platform.expressions``.

The implementation moved to ``platform`` because two domains need it (the engine
for edge ``mapping``/``condition``; the node runtime for ``http_manifest``
templates) and neither domain may import the other (import-linter Contract 2).
This module stays as the engine's historical import path so existing callers —
and the engine's lazy import in ``engine.py`` — keep working unchanged.
"""

from __future__ import annotations

from axiom.platform.expressions import (
    JsonValue,
    evaluate_condition,
    resolve_mapping,
    resolve_value,
)

__all__ = [
    "JsonValue",
    "evaluate_condition",
    "resolve_mapping",
    "resolve_value",
]
