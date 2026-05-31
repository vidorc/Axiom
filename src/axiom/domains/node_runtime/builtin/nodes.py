"""Built-in reference nodes (PHASE_1.md §7.3 — "be your own first node author").

These are real ``BaseNode`` implementations, not toys: they exercise every part
of the SDK contract (validate, execute, ctx.http, ctx.cost, the error taxonomy,
cooperative cancellation) and are what the engine's end-to-end tests run. They
also serve as the worked examples a node author copies from.

Kept intentionally provider-agnostic — a real provider node (Apollo, OpenAI) is
the same shape with a real URL and auth; these prove the machinery without
needing a credential or a network in tests.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from axiom.sdk import (
    BaseNode,
    ErrorClass,
    JsonObject,
    JsonValue,
    NodeError,
    ValidationResult,
)

if TYPE_CHECKING:
    from axiom.sdk import ExecutionContext


class EchoNode(BaseNode):
    """Returns its inputs unchanged. The simplest possible data-flow node.

    Useful as a graph connector and as the canonical engine test node: whatever
    flows in, flows out, so data movement through edges is observable.
    """

    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        return dict(inputs)


class SetFieldsNode(BaseNode):
    """Merges a static ``fields`` object onto the input (a 'transform' node).

    Config: ``{"fields": {<key>: <value>, ...}}``. The declared fields override
    same-named input keys. This is the building block for shaping data between
    provider calls without writing code.
    """

    def validate(self, inputs: JsonObject) -> ValidationResult:
        fields = inputs.get("fields")
        if fields is not None and not isinstance(fields, dict):
            return ValidationResult.failure("`fields` must be an object")
        return ValidationResult.success()

    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        fields = inputs.get("fields") or {}
        out = {k: v for k, v in inputs.items() if k != "fields"}
        if isinstance(fields, dict):
            out.update(fields)
        return out


class DelayNode(BaseNode):
    """Sleeps for ``seconds`` (default 0), checking cancellation as it waits.

    Demonstrates cooperative cancellation (SDK_SPEC.md §4.4): a long node polls
    ``ctx.cancel.is_cancelled()`` and aborts promptly. Also handy for testing
    lease/timeout behaviour without a real slow provider.
    """

    def validate(self, inputs: JsonObject) -> ValidationResult:
        seconds = inputs.get("seconds", 0)
        if not isinstance(seconds, (int, float)) or seconds < 0:
            return ValidationResult.failure("`seconds` must be a non-negative number")
        return ValidationResult.success()

    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        seconds_raw = inputs.get("seconds", 0)
        seconds = float(seconds_raw) if isinstance(seconds_raw, (int, float)) else 0.0
        # Sleep in small slices so cancellation is responsive.
        slept = 0.0
        slice_s = 0.05
        while slept < seconds:
            if ctx.cancel.is_cancelled():
                raise NodeError(ErrorClass.INTERNAL, "cancelled during delay")
            await asyncio.sleep(min(slice_s, seconds - slept))
            slept += slice_s
        return {"slept_seconds": seconds}


class HttpRequestNode(BaseNode):
    """A generic HTTP request node — the realistic reference.

    Config / inputs:
      * ``method`` (default GET), ``url`` (required)
      * ``headers`` (object), ``query`` (object), ``json`` (request body object)
      * ``timeout_seconds`` (default 30)

    Output: ``{"status": int, "headers": {...}, "body": <parsed json or text>}``.

    Maps provider responses onto the error taxonomy exactly as the design
    prescribes (SDK_SPEC.md §4.3): 429 → rate_limited (honors Retry-After), 5xx →
    provider_error, 401/403 → auth_error, other 4xx → invalid_input, network
    errors → timeout. This is the same mapping an http_manifest node's ``errors``
    block encodes declaratively.
    """

    def validate(self, inputs: JsonObject) -> ValidationResult:
        errors: list[str] = []
        url = inputs.get("url")
        if not url or not isinstance(url, str):
            errors.append("`url` is required and must be a string")
        method = inputs.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in (
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
        ):
            errors.append(f"unsupported HTTP method: {method!r}")
        return ValidationResult.failure(*errors) if errors else ValidationResult.success()

    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        import httpx

        method = str(inputs.get("method", "GET")).upper()
        url = str(inputs["url"])
        headers_raw = inputs.get("headers")
        query_raw = inputs.get("query")
        json_body = inputs.get("json")

        # httpx wants str->str mappings; coerce the JSON values to strings.
        headers = (
            {str(k): str(v) for k, v in headers_raw.items()}
            if isinstance(headers_raw, dict)
            else None
        )
        params = (
            {str(k): str(v) for k, v in query_raw.items()} if isinstance(query_raw, dict) else None
        )

        ctx.log.info("http.request", method=method, url=url)
        try:
            response = await ctx.http.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise NodeError(ErrorClass.TIMEOUT, f"request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            # Connection errors etc. — transient, retryable as a provider error.
            raise NodeError(ErrorClass.PROVIDER_ERROR, f"request failed: {exc}") from exc

        # The call cost one request unit; provider-specific cost models refine
        # this from usage in the response (tokens/credits) — here, one call.
        ctx.cost.record(1, "call")

        self._raise_for_status(response)

        body: JsonValue
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            body = response.json()
        else:
            body = response.text

        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }

    @staticmethod
    def _raise_for_status(response: object) -> None:
        """Translate an HTTP status into the node error taxonomy."""
        status = getattr(response, "status_code", 200)
        if status < 400:
            return
        if status == 429:
            retry_after = _parse_retry_after(response)
            raise NodeError(
                ErrorClass.RATE_LIMITED,
                "rate limited (429)",
                retry_after_seconds=retry_after,
            )
        if status in (401, 403):
            raise NodeError(ErrorClass.AUTH_ERROR, f"auth failed ({status})")
        if status == 404:
            raise NodeError(ErrorClass.NOT_FOUND, "not found (404)")
        if 500 <= status < 600:
            raise NodeError(ErrorClass.PROVIDER_ERROR, f"provider error ({status})")
        # Other 4xx — the request itself is wrong; terminal.
        raise NodeError(ErrorClass.INVALID_INPUT, f"client error ({status})")


def _parse_retry_after(response: object) -> float | None:
    headers = getattr(response, "headers", {})
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        return float(raw)  # delta-seconds form
    except (TypeError, ValueError):
        return None  # HTTP-date form not parsed here; backoff falls back to policy
