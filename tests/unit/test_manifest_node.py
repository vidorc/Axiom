"""Unit tests for the http_manifest node executor (SDK_SPEC.md §3).

Drives a manifest-built node with a real ``NodeExecutionContext`` (scoped
credentials + cost ledger) over an httpx ``MockTransport``, so we can assert the
exact request a provider would see: the injected auth header, the templated body,
the mapped outputs, recorded cost, and the error-taxonomy mapping.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from axiom.domains.node_runtime.context import NodeExecutionContext, RunContext
from axiom.domains.node_runtime.manifest import parse_manifest
from axiom.domains.node_runtime.manifest_node import build_manifest_node
from axiom.sdk import ErrorClass, NodeError

MANIFEST = {
    "id": "axiom/apollo-enrich",
    "version": "1.4.0",
    "kind": "http_manifest",
    "category": "enrichment",
    "auth": [
        {
            "provider": "apollo",
            "inject": {"type": "header", "name": "X-Api-Key", "value": "{{ credentials.apollo }}"},
        }
    ],
    "inputs": {
        "type": "object",
        "required": ["email"],
        "properties": {"email": {"type": "string"}},
    },
    "request": {
        "method": "POST",
        "url": "https://api.apollo.example/v1/people/match",
        "body": {"json": {"email": "{{ inputs.email }}", "reveal": "{{ inputs.reveal }}"}},
    },
    "outputs": {
        "type": "object",
        "properties": {
            "full_name": {"from": "{{ response.person.name }}"},
            "company": {"from": "{{ response.person.organization.name }}"},
        },
    },
    "errors": [
        {"when": {"status": 429}, "then": {"class": "rate_limited"}},
        {"when": {"status": [500, 503]}, "then": {"class": "provider_error"}},
        {"when": {"status": 422}, "then": {"class": "invalid_input"}},
    ],
    "cost_model": {"unit": "credit", "per_call": 1},
}

SECRET = "sk-apollo-DO-NOT-LEAK-12345"


def _ctx(handler, *, secrets: dict[str, str] | None = None) -> NodeExecutionContext:
    """A real execution context with an httpx client backed by ``handler``."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run = RunContext(run_id=uuid4(), node_id="n1", attempt=1, org_id=uuid4())
    return NodeExecutionContext.build(
        run=run, secrets=secrets if secrets is not None else {"apollo": SECRET}, http=client
    )


@pytest.mark.unit
async def test_injects_credential_and_templates_body() -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(
            200,
            json={"person": {"name": "Ada Lovelace", "organization": {"name": "Analytical Eng"}}},
        )

    node = build_manifest_node(parse_manifest(MANIFEST))()
    ctx = _ctx(handler)
    async with ctx.http:
        out = await node.execute(ctx, {"email": "ada@example.com", "reveal": True})

    req = seen["req"]
    # The declared credential was injected as the configured header — and the
    # plaintext is the real secret (proves scoping resolved it).
    assert req.headers["X-Api-Key"] == SECRET
    assert req.method == "POST"
    assert str(req.url) == "https://api.apollo.example/v1/people/match"
    # The json body was templated from inputs (type preserved for the bool).
    import json as _json

    body = _json.loads(req.content)
    assert body == {"email": "ada@example.com", "reveal": True}
    # Outputs mapped from the response via {{ response.* }} templates.
    assert out == {"full_name": "Ada Lovelace", "company": "Analytical Eng"}
    # Cost recorded from the cost_model.
    assert ctx.cost.entries == [(1, "credit")]


@pytest.mark.unit
async def test_query_injection_variant() -> None:
    manifest = {
        **MANIFEST,
        "auth": [
            {
                "provider": "apollo",
                "inject": {"type": "query", "name": "api_key", "value": "{{ credentials.apollo }}"},
            }
        ],
        "request": {
            "method": "GET",
            "url": "https://api.apollo.example/v1/lookup",
            "query": {"email": "{{ inputs.email }}"},
        },
    }
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, json={})

    node = build_manifest_node(parse_manifest(manifest))()
    ctx = _ctx(handler)
    async with ctx.http:
        await node.execute(ctx, {"email": "ada@example.com"})

    params = dict(seen["req"].url.params)
    assert params["api_key"] == SECRET
    assert params["email"] == "ada@example.com"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ErrorClass.RATE_LIMITED),
        (500, ErrorClass.PROVIDER_ERROR),
        (503, ErrorClass.PROVIDER_ERROR),
        (422, ErrorClass.INVALID_INPUT),
        (401, ErrorClass.AUTH_ERROR),  # not in errors block → default mapping
        (404, ErrorClass.NOT_FOUND),  # not in errors block → default mapping
    ],
)
async def test_error_status_maps_to_taxonomy(status: int, expected: ErrorClass) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "x"})

    node = build_manifest_node(parse_manifest(MANIFEST))()
    ctx = _ctx(handler)
    async with ctx.http:
        with pytest.raises(NodeError) as ei:
            await node.execute(ctx, {"email": "ada@example.com"})
    assert ei.value.error_class is expected


@pytest.mark.unit
async def test_rate_limited_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "12"}, json={})

    node = build_manifest_node(parse_manifest(MANIFEST))()
    ctx = _ctx(handler)
    async with ctx.http:
        with pytest.raises(NodeError) as ei:
            await node.execute(ctx, {"email": "ada@example.com"})
    assert ei.value.error_class is ErrorClass.RATE_LIMITED
    assert ei.value.retry_after_seconds == 12.0


@pytest.mark.unit
async def test_no_cost_recorded_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    node = build_manifest_node(parse_manifest(MANIFEST))()
    ctx = _ctx(handler)
    async with ctx.http:
        with pytest.raises(NodeError):
            await node.execute(ctx, {"email": "ada@example.com"})
    # Cost is recorded only after a successful call; an error path records nothing.
    assert ctx.cost.entries == []


@pytest.mark.unit
def test_validate_flags_missing_required_input() -> None:
    node = build_manifest_node(parse_manifest(MANIFEST))()
    result = node.validate({})  # missing required `email`
    assert result.ok is False
    assert any("email" in e for e in result.errors)


@pytest.mark.unit
async def test_undeclared_credential_is_terminal_auth_error() -> None:
    # The manifest declares `apollo`, but the run bound no such secret → the
    # scoped accessor raises, surfaced as a terminal auth error (never a silent
    # empty key sent to the provider).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    node = build_manifest_node(parse_manifest(MANIFEST))()
    ctx = _ctx(handler, secrets={})  # no apollo credential
    async with ctx.http:
        with pytest.raises(NodeError) as ei:
            await node.execute(ctx, {"email": "ada@example.com"})
    assert ei.value.error_class is ErrorClass.AUTH_ERROR


@pytest.mark.unit
async def test_outputs_default_to_body_when_unmapped() -> None:
    manifest = {k: v for k, v in MANIFEST.items() if k != "outputs"}
    payload = {"anything": [1, 2, 3]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    node = build_manifest_node(parse_manifest(manifest))()
    ctx = _ctx(handler)
    async with ctx.http:
        out = await node.execute(ctx, {"email": "ada@example.com"})
    assert out == {"body": payload}
