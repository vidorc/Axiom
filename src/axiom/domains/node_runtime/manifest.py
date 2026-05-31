"""Parse a node manifest into a typed, validated value object (SDK_SPEC.md §3, §5).

A manifest is the declarative description of a node — "call this API with these
inputs, map the response to these outputs." This module turns the raw dict (from
YAML/JSON) into a frozen :class:`Manifest` the runtime can execute, failing loudly
with :class:`ManifestError` on anything malformed. Parsing is separate from
execution so the same parsed object backs both ``axiom validate`` (authoring) and
the runtime executor (SDK_SPEC.md §8).

Scope: this models the ``http_manifest`` kind in full (request, auth injection,
output mapping, error taxonomy, cost, rate-limit hints) plus the common envelope.
``code`` and ``mcp`` kinds share the envelope but are loaded differently (a Python
entrypoint, an MCP tool schema) and are parsed by their own loaders later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from axiom.sdk import RETRYABLE_CLASSES, ErrorClass

# The manifest format version this parser understands (SDK_SPEC.md §6.4). A
# manifest declaring a different schema is rejected rather than silently
# misinterpreted — the format is a stable, versioned contract.
SUPPORTED_MANIFEST_SCHEMA = "1"

_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_INJECT_TYPES = frozenset({"header", "query"})


class ManifestError(Exception):
    """A manifest is malformed or declares something unsupported.

    Raised at parse time with an actionable message (which field, what was wrong)
    — the same class ``axiom validate`` surfaces to a node author (SDK_SPEC.md §8:
    great error messages are the difference between 10 minutes and an abandoned
    afternoon).
    """


@dataclass(frozen=True, slots=True)
class AuthInjection:
    """How a declared credential is injected into the request (SDK_SPEC.md §3.1).

    ``provider`` names the vault credential the node is scoped to; ``where`` is
    ``header`` or ``query``; ``name`` is the header/param name; ``value_template``
    is the ``{{ credentials.<provider> }}`` expression resolved at exec time so the
    plaintext only ever exists in worker memory.
    """

    provider: str
    where: str
    name: str
    value_template: str


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """The HTTP request a manifest node makes, as templates resolved per run."""

    method: str
    url_template: str
    headers: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    json_body: Any = None  # may be a dict/list/scalar of templates, or None
    pagination_type: str = "none"


@dataclass(frozen=True, slots=True)
class OutputField:
    """One output field and the ``{{ response.* }}`` expression that fills it."""

    name: str
    from_template: str


@dataclass(frozen=True, slots=True)
class ErrorRule:
    """Maps a response status (or statuses) to an error class + retryable flag.

    ``statuses`` is the set this rule matches; ``error_class`` is the taxonomy
    class to raise; ``retryable`` overrides the class default when set (the
    manifest is explicit, per SDK_SPEC.md §3.1).
    """

    statuses: frozenset[int]
    error_class: ErrorClass
    retryable: bool


@dataclass(frozen=True, slots=True)
class CostModel:
    """How the node reports cost (SDK_SPEC.md §5; DOMAIN_MODEL.md §8)."""

    unit: str = "call"
    per_call: int = 0


@dataclass(frozen=True, slots=True)
class Manifest:
    """A fully-parsed, validated node manifest (the runtime's vocabulary)."""

    id: str
    version: str
    kind: str
    category: str
    display: dict[str, Any]
    required_inputs: tuple[str, ...]
    input_properties: dict[str, Any]
    auth: tuple[AuthInjection, ...]
    request: RequestSpec
    outputs: tuple[OutputField, ...]
    errors: tuple[ErrorRule, ...]
    cost_model: CostModel
    rate_limit_hint: dict[str, Any] = field(default_factory=dict)

    @property
    def declared_providers(self) -> tuple[str, ...]:
        """Provider names this node is scoped to — the only secrets it receives."""
        return tuple(a.provider for a in self.auth)


def parse_manifest(raw: dict[str, Any]) -> Manifest:
    """Parse + validate a raw manifest dict into a :class:`Manifest`.

    Validates the common envelope (id, version, kind, manifest_schema) and, for
    the ``http_manifest`` kind, the request/auth/outputs/errors blocks. Raises
    :class:`ManifestError` with a specific message on the first structural problem.
    """
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a mapping")

    schema = str(raw.get("manifest_schema", SUPPORTED_MANIFEST_SCHEMA))
    if schema != SUPPORTED_MANIFEST_SCHEMA:
        raise ManifestError(
            f"unsupported manifest_schema {schema!r} "
            f"(this runtime understands {SUPPORTED_MANIFEST_SCHEMA!r})"
        )

    node_id = _require_str(raw, "id")
    version = _require_str(raw, "version")
    kind = _require_str(raw, "kind")
    if kind != "http_manifest":
        # The parser models the declarative kind; code/mcp have their own loaders.
        raise ManifestError(
            f"parse_manifest handles kind 'http_manifest', got {kind!r} "
            "(code/mcp nodes are loaded by their own loaders)"
        )

    inputs_schema = raw.get("inputs") or {}
    if not isinstance(inputs_schema, dict):
        raise ManifestError("`inputs` must be a JSONSchema object")
    required_inputs = tuple(inputs_schema.get("required", ()) or ())
    input_properties = inputs_schema.get("properties", {}) or {}

    return Manifest(
        id=node_id,
        version=version,
        kind=kind,
        category=str(raw.get("category", "custom")),
        display=raw.get("display", {}) or {},
        required_inputs=required_inputs,
        input_properties=input_properties,
        auth=_parse_auth(raw.get("auth", []) or []),
        request=_parse_request(raw),
        outputs=_parse_outputs(raw.get("outputs", {}) or {}),
        errors=_parse_errors(raw.get("errors", []) or []),
        cost_model=_parse_cost_model(raw.get("cost_model", {}) or {}),
        rate_limit_hint=raw.get("rate_limit_hint", {}) or {},
    )


# ── Section parsers ─────────────────────────────────────────────────────────────


def _parse_auth(raw_auth: Any) -> tuple[AuthInjection, ...]:
    if not isinstance(raw_auth, list):
        raise ManifestError("`auth` must be a list")
    injections: list[AuthInjection] = []
    seen_targets: set[tuple[str, str]] = set()
    for entry in raw_auth:
        if not isinstance(entry, dict):
            raise ManifestError("each `auth` entry must be a mapping")
        provider = _require_str(entry, "provider", ctx="auth entry")
        inject = entry.get("inject")
        if inject is None:
            # A provider declared with no injection still scopes the credential;
            # the node may use it via ctx.credentials() in a (future) code path.
            continue
        if not isinstance(inject, dict):
            raise ManifestError(f"auth.inject for {provider!r} must be a mapping")
        where = str(inject.get("type", "header")).lower()
        if where not in _INJECT_TYPES:
            raise ManifestError(
                f"auth.inject.type for {provider!r} must be one of {sorted(_INJECT_TYPES)}, "
                f"got {where!r}"
            )
        name = _require_str(inject, "name", ctx=f"auth.inject for {provider!r}")
        # Reject two injections at the same target — the executor would otherwise
        # silently overwrite the first, sending only the second credential. Header
        # names are case-insensitive (HTTP), query params are not.
        target_key = (where, name.lower() if where == "header" else name)
        if target_key in seen_targets:
            raise ManifestError(
                f"two auth injections target the same {where} {name!r}; "
                "each credential must inject into a distinct header/param"
            )
        seen_targets.add(target_key)
        value = str(inject.get("value", f"{{{{ credentials.{provider} }}}}"))
        injections.append(
            AuthInjection(provider=provider, where=where, name=name, value_template=value)
        )
    return tuple(injections)


def _parse_request(raw: dict[str, Any]) -> RequestSpec:
    req = raw.get("request")
    if not isinstance(req, dict):
        raise ManifestError("`request` block is required for an http_manifest node")
    method = str(req.get("method", "GET")).upper()
    if method not in _HTTP_METHODS:
        raise ManifestError(f"request.method {method!r} not in {sorted(_HTTP_METHODS)}")
    url = _require_str(req, "url", ctx="request")

    body = req.get("body") or {}
    if body and not isinstance(body, dict):
        raise ManifestError("request.body must be a mapping (e.g. {json: {...}})")
    json_body = body.get("json") if isinstance(body, dict) else None

    pagination = req.get("pagination") or {}
    pag_type = (
        str(pagination.get("type", "none")).lower() if isinstance(pagination, dict) else "none"
    )
    if pag_type != "none":
        # Cursor/offset/link pagination is a Phase-2 survey item (SDK_SPEC.md §9.2).
        # Be explicit rather than silently fetching only the first page.
        raise ManifestError(
            f"pagination.type {pag_type!r} is not yet supported; only 'none' is "
            "implemented (cursor/offset pagination is a Phase 2 item)"
        )

    headers = req.get("headers") or {}
    query = req.get("query") or {}
    if not isinstance(headers, dict) or not isinstance(query, dict):
        raise ManifestError("request.headers and request.query must be mappings")

    return RequestSpec(
        method=method,
        url_template=url,
        headers=headers,
        query=query,
        json_body=json_body,
        pagination_type=pag_type,
    )


def _parse_outputs(raw_outputs: Any) -> tuple[OutputField, ...]:
    if not isinstance(raw_outputs, dict):
        raise ManifestError("`outputs` must be a JSONSchema object")
    props = raw_outputs.get("properties", raw_outputs)
    if not isinstance(props, dict):
        raise ManifestError("`outputs.properties` must be a mapping")
    fields: list[OutputField] = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        from_template = spec.get("from")
        if from_template is None:
            # No mapping → the field isn't extracted from the response; skip it.
            continue
        fields.append(OutputField(name=str(name), from_template=str(from_template)))
    return tuple(fields)


def _parse_errors(raw_errors: Any) -> tuple[ErrorRule, ...]:
    if not isinstance(raw_errors, list):
        raise ManifestError("`errors` must be a list")
    rules: list[ErrorRule] = []
    for entry in raw_errors:
        if not isinstance(entry, dict):
            raise ManifestError("each `errors` entry must be a mapping")
        when = entry.get("when") or {}
        then = entry.get("then") or {}
        if not isinstance(when, dict) or not isinstance(then, dict):
            raise ManifestError("each `errors` entry needs `when` and `then` mappings")
        status = when.get("status")
        statuses = _coerce_statuses(status)
        class_name = _require_str(then, "class", ctx="errors.then")
        try:
            error_class = ErrorClass(class_name)
        except ValueError as exc:
            valid = sorted(c.value for c in ErrorClass)
            raise ManifestError(
                f"errors.then.class {class_name!r} is not a valid error class (one of {valid})"
            ) from exc
        retryable = bool(then.get("retryable", error_class in RETRYABLE_CLASSES))
        rules.append(ErrorRule(statuses=statuses, error_class=error_class, retryable=retryable))
    return tuple(rules)


def _parse_cost_model(raw_cost: Any) -> CostModel:
    if not isinstance(raw_cost, dict):
        raise ManifestError("`cost_model` must be a mapping")
    return CostModel(
        unit=str(raw_cost.get("unit", "call")),
        per_call=int(raw_cost.get("per_call", 0) or 0),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _require_str(d: dict[str, Any], key: str, *, ctx: str | None = None) -> str:
    value = d.get(key)
    if not isinstance(value, str) or not value:
        where = f" in {ctx}" if ctx else ""
        raise ManifestError(f"`{key}` is required and must be a non-empty string{where}")
    return value


def _coerce_statuses(status: Any) -> frozenset[int]:
    """Accept a single status or a list of statuses; validate they are ints."""
    if status is None:
        return frozenset()
    items = status if isinstance(status, list) else [status]
    out: set[int] = set()
    for s in items:
        try:
            out.add(int(s))
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"errors.when.status must be int(s), got {s!r}") from exc
    return frozenset(out)
