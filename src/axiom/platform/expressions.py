"""The minimal `{{ }}` expression layer (DOMAIN_MODEL.md §2, SDK_SPEC.md §3.3).

Deliberately tiny and non-Turing-complete (BLUEPRINT.md §3.4: "no arbitrary
code, no loops, no side effects"). An expression is a string that may contain
``{{ dotted.path }}`` references resolved against a context dict. That's it —
field access plus a couple of safe formatting helpers. When real logic is
needed, the design escalates to a code node rather than growing this into a
scripting language.

It lives in ``platform`` because *two* domains need it and neither may import the
other: the execution engine resolves edge ``mapping``/``condition`` expressions,
and the node runtime resolves an ``http_manifest`` node's request/output
templates (``{{ inputs.x }}``, ``{{ credentials.apollo }}``, ``{{ response.y }}``).
Putting the one implementation here keeps it a single, well-tested home that both
domains import downward, rather than one domain reaching across to the other
(which import-linter Contract 2 forbids).

Two resolution shapes are supported:

  * **Whole-value reference** — a string that is *exactly* one ``{{ ... }}``
    returns the referenced value with its original type preserved
    (``"{{ inputs.count }}"`` → the int ``5``, not ``"5"``).
  * **Interpolation** — ``{{ ... }}`` embedded in surrounding text returns a
    string with each reference stringified (``"Hi {{ inputs.name }}"`` →
    ``"Hi Ada"``).

Resolution never raises on a missing path; it yields ``None`` (whole-value) or
empty string (interpolation), matching the ``default`` helper's intent and
keeping a mapping typo from crashing a run. Authoring-time validation is where
unknown references should be caught loudly; run time is forgiving.
"""

from __future__ import annotations

import re
from typing import Any

# A JSON-compatible value. Defined locally (rather than imported from
# ``axiom.sdk``) so ``platform`` stays free of any upward dependency — the
# expression engine is pure infrastructure and the SDK is a separate leaf.
type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

# A single {{ ... }} token. Non-greedy so multiple tokens in one string don't
# merge. Whitespace around the body is trimmed.
_TOKEN = re.compile(r"\{\{\s*(?P<body>.*?)\s*\}\}")


def _lookup(path: str, context: dict[str, Any]) -> JsonValue:
    """Resolve a dotted path against the context, returning None if any hop
    is missing. Supports the ``| helper`` suffix for a default value, e.g.
    ``inputs.title | default:Unknown``."""
    default: JsonValue = None
    if "|" in path:
        path, _, helper = path.partition("|")
        path = path.strip()
        helper = helper.strip()
        if helper.startswith("default:"):
            default = helper[len("default:") :].strip()

    cur: Any = context
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    if cur is None:
        return default
    return cur  # type: ignore[no-any-return]


def resolve_value(expr: JsonValue, context: dict[str, Any]) -> JsonValue:
    """Resolve a single mapping value (string expr, or a literal passed through).

    Non-string literals (numbers, bools, nested dicts/lists) are returned as-is
    so a mapping can carry constants. Strings are run through the token engine.
    """
    if not isinstance(expr, str):
        return expr

    # Whole-value reference: preserve the referenced value's type.
    whole = _TOKEN.fullmatch(expr.strip())
    if whole:
        return _lookup(whole.group("body"), context)

    # Interpolation: stringify each token in place.
    def _sub(m: re.Match[str]) -> str:
        val = _lookup(m.group("body"), context)
        return "" if val is None else str(val)

    return _TOKEN.sub(_sub, expr)


def resolve_mapping(mapping: dict[str, JsonValue], context: dict[str, Any]) -> dict[str, JsonValue]:
    """Resolve every value in an edge mapping against the context."""
    return {key: resolve_value(val, context) for key, val in mapping.items()}


def evaluate_condition(condition: str | None, context: dict[str, Any]) -> bool:
    """Evaluate an edge ``condition`` for truthiness (None → always true).

    The condition is resolved like any expression and judged by Python
    truthiness, with the common string cases normalized: ``"false"``, ``"0"``,
    ``""`` and ``"none"`` are falsy. This is intentionally simple — a branch that
    needs real predicate logic uses a code node that returns a boolean field the
    condition then references.
    """
    if condition is None:
        return True
    value = resolve_value(condition, context)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "none", "null")
    return bool(value)
