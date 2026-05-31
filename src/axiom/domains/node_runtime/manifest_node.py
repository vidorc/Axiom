"""Execute an ``http_manifest`` node — the declarative node kind (SDK_SPEC.md §3).

:func:`build_manifest_node` turns a parsed :class:`Manifest` into a zero-arg
``BaseNode`` subclass the registry can instantiate like any code node. The
resulting node carries *no* hand-written logic — at execution it:

  1. Builds a template context of ``{inputs, credentials}`` — credentials resolved
     from ``ctx.credentials(provider)`` for *only* the providers the manifest
     declared (SECURITY.md §6.2), so plaintext exists in memory just for this call.
  2. Resolves the request URL / headers / query / json body from their ``{{ }}``
     templates, and injects each declared credential into the header or query the
     ``auth.inject`` block names.
  3. Calls ``ctx.http`` (the instrumented, SSRF-filtered client), then maps the
     response status to the error taxonomy via the manifest ``errors`` block —
     raising a typed ``NodeError`` the engine turns into retry-or-terminal.
  4. Maps the response body to outputs via the ``{{ response.* }}`` templates and
     records cost from ``cost_model``.

This is "~80% of integrations are a manifest, not a program" (SDK_SPEC.md §2) made
real: Apollo, Prospeo, Smartlead, and most provider calls are now a YAML file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from axiom.domains.node_runtime.manifest import ErrorRule, Manifest
from axiom.platform.expressions import resolve_value
from axiom.sdk import BaseNode, ErrorClass, JsonObject, JsonValue, NodeError, ValidationResult

if TYPE_CHECKING:
    from axiom.sdk import ExecutionContext


class _ManifestNode(BaseNode):
    """A code-free node whose behavior is entirely the bound manifest.

    Subclasses (one per manifest, produced by :func:`build_manifest_node`) set
    ``_manifest``. The class is constructed with no args by the registry, exactly
    like a hand-written node.
    """

    _manifest: Manifest

    def validate(self, inputs: JsonObject) -> ValidationResult:
        # Structural required-field check (the JSONSchema's semantic checks are a
        # Phase-2 validation pass; this catches the common "forgot a required
        # input" without spending money on the call).
        missing = [name for name in self._manifest.required_inputs if inputs.get(name) is None]
        if missing:
            return ValidationResult.failure(
                *(f"required input {name!r} is missing" for name in missing)
            )
        return ValidationResult.success()

    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        manifest = self._manifest

        # 1) Build the template context. Resolve ONLY declared providers — an
        # undeclared lookup raises in ctx.credentials, which is the scoping
        # guarantee, surfaced here as a terminal auth error.
        credentials: dict[str, str] = {}
        for provider in manifest.declared_providers:
            try:
                credentials[provider] = ctx.credentials(provider)
            except KeyError as exc:
                raise NodeError(
                    ErrorClass.AUTH_ERROR,
                    f"credential {provider!r} declared by the manifest is not bound for this run",
                ) from exc
        context: dict[str, Any] = {"inputs": dict(inputs), "credentials": credentials}

        # 2) Resolve the request from its templates + inject auth.
        url = str(resolve_value(manifest.request.url_template, context))
        headers = _resolve_str_map(manifest.request.headers, context)
        params = _resolve_str_map(manifest.request.query, context)
        json_body = (
            _resolve_deep(manifest.request.json_body, context)
            if manifest.request.json_body is not None
            else None
        )
        for inj in manifest.auth:
            value = str(resolve_value(inj.value_template, context))
            target = headers if inj.where == "header" else params
            target[inj.name] = value

        # Log the request with only scheme+host — never the full URL, headers, or
        # params. Those can carry an injected credential (an author may template
        # `{{ credentials.x }}` into a query param or path), and the URL host is
        # the safe, useful tracing value (it was already egress-validated).
        ctx.log.info(
            "manifest.request",
            node=manifest.id,
            method=manifest.request.method,
            host=_safe_host(url),
        )

        # 3) Make the call through the instrumented client.
        try:
            response = await ctx.http.request(
                manifest.request.method,
                url,
                headers=headers or None,
                params=params or None,
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise NodeError(ErrorClass.TIMEOUT, f"request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise NodeError(ErrorClass.PROVIDER_ERROR, f"request failed: {exc}") from exc

        # 4) Map status → error taxonomy (manifest errors block, then defaults).
        self._raise_for_status(response, manifest)

        # Success: record cost (a completed call) and map outputs.
        if manifest.cost_model.per_call:
            ctx.cost.record(manifest.cost_model.per_call, manifest.cost_model.unit)

        body = _parse_body(response)
        return _map_outputs(manifest, body)

    @classmethod
    def _raise_for_status(cls, response: httpx.Response, manifest: Manifest) -> None:
        """Translate the response status into a typed NodeError, or return on 2xx.

        The manifest ``errors`` rules win; for a status no rule covers, fall back
        to the standard taxonomy so an incomplete ``errors`` block never lets an
        error status pass as success.
        """
        status = response.status_code
        if status < 400:
            return

        rule = _match_rule(manifest.errors, status)
        if rule is not None:
            retry_after = (
                _retry_after(response) if rule.error_class is ErrorClass.RATE_LIMITED else None
            )
            raise NodeError(
                rule.error_class,
                f"{manifest.id}: provider returned {status}",
                retry_after_seconds=retry_after,
            )

        # Default mapping (matches the generic HTTP node) for uncovered statuses.
        if status == 429:
            raise NodeError(
                ErrorClass.RATE_LIMITED,
                f"rate limited ({status})",
                retry_after_seconds=_retry_after(response),
            )
        if status in (401, 403):
            raise NodeError(ErrorClass.AUTH_ERROR, f"auth failed ({status})")
        if status == 404:
            raise NodeError(ErrorClass.NOT_FOUND, f"not found ({status})")
        if 500 <= status < 600:
            raise NodeError(ErrorClass.PROVIDER_ERROR, f"provider error ({status})")
        raise NodeError(ErrorClass.INVALID_INPUT, f"client error ({status})")


def build_manifest_node(manifest: Manifest) -> type[BaseNode]:
    """Create a zero-arg ``BaseNode`` subclass bound to ``manifest``.

    The returned class is registered in the ``NodeRegistry`` under the manifest's
    id/version exactly like a code node; the registry instantiates it with no
    args per execution (nodes are stateless).
    """
    node_cls = type(
        _class_name(manifest.id),
        (_ManifestNode,),
        {"_manifest": manifest, "__doc__": manifest.display.get("summary", manifest.id)},
    )
    return node_cls


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _safe_host(url: str) -> str:
    """Scheme + host of a URL, dropping path/query/fragment — log-safe.

    The query or path may contain a credential an author templated into the URL,
    so only the scheme+host (already egress-validated) is ever logged.
    """
    parts = urlsplit(url)
    host = parts.hostname or "?"
    return f"{parts.scheme}://{host}" if parts.scheme else host


def _match_rule(rules: tuple[ErrorRule, ...], status: int) -> ErrorRule | None:
    for rule in rules:
        if status in rule.statuses:
            return rule
    return None


def _resolve_str_map(template_map: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
    """Resolve a header/query template map to a str→str map (httpx's shape)."""
    out: dict[str, str] = {}
    for key, val in template_map.items():
        resolved = resolve_value(val, context)
        if resolved is not None:
            out[str(key)] = str(resolved)
    return out


def _resolve_deep(value: JsonValue, context: dict[str, Any]) -> JsonValue:
    """Recursively resolve templates inside a json body (objects, arrays, scalars)."""
    if isinstance(value, dict):
        return {k: _resolve_deep(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_deep(v, context) for v in value]
    return resolve_value(value, context)


def _parse_body(response: httpx.Response) -> JsonValue:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type or "+json" in content_type:
        try:
            parsed: JsonValue = response.json()
            return parsed
        except ValueError:
            return response.text
    return response.text


def _map_outputs(manifest: Manifest, body: JsonValue) -> JsonObject:
    """Extract outputs from the response body via the manifest's ``from`` templates.

    If the manifest declares no output mappings, return the parsed body under a
    ``body`` key so a downstream node still receives the data.
    """
    if not manifest.outputs:
        return {"body": body}
    context = {"response": body}
    return {field.name: resolve_value(field.from_template, context) for field in manifest.outputs}


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None  # HTTP-date form not parsed; backoff falls back to policy


def _class_name(node_id: str) -> str:
    """A valid Python class name derived from a node id like ``axiom/apollo-enrich``."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in node_id)
    return "".join(part.capitalize() for part in cleaned.split()) + "ManifestNode" or "ManifestNode"
