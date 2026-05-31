"""Unit tests for the http_manifest parser (SDK_SPEC.md §3, §5).

Proves the parser turns a well-formed manifest into the right typed
:class:`Manifest`, and rejects malformed input with an actionable
:class:`ManifestError` — the same errors ``axiom validate`` surfaces to a node
author (SDK_SPEC.md §8).
"""

from __future__ import annotations

import pytest

from axiom.domains.node_runtime.manifest import ManifestError, parse_manifest
from axiom.sdk import ErrorClass

# The illustrative Apollo manifest from SDK_SPEC.md §3.1, trimmed to essentials.
APOLLO = {
    "id": "axiom/apollo-enrich",
    "version": "1.4.0",
    "kind": "http_manifest",
    "manifest_schema": "1",
    "category": "enrichment",
    "display": {"label": "Apollo — Enrich Person"},
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
        "url": "https://api.apollo.io/v1/people/match",
        "body": {"json": {"email": "{{ inputs.email }}"}},
        "pagination": {"type": "none"},
    },
    "outputs": {
        "type": "object",
        "properties": {
            "full_name": {"type": "string", "from": "{{ response.person.name }}"},
            "title": {"type": "string", "from": "{{ response.person.title }}"},
        },
    },
    "errors": [
        {"when": {"status": 429}, "then": {"class": "rate_limited", "retryable": True}},
        {"when": {"status": [500, 502, 503]}, "then": {"class": "provider_error"}},
        {"when": {"status": 401}, "then": {"class": "auth_error", "retryable": False}},
    ],
    "cost_model": {"unit": "credit", "per_call": 1},
    "rate_limit_hint": {"rpm": 600, "concurrency": 5},
}


@pytest.mark.unit
def test_parses_envelope_fields() -> None:
    m = parse_manifest(APOLLO)
    assert m.id == "axiom/apollo-enrich"
    assert m.version == "1.4.0"
    assert m.kind == "http_manifest"
    assert m.category == "enrichment"
    assert m.required_inputs == ("email",)


@pytest.mark.unit
def test_parses_auth_injection() -> None:
    m = parse_manifest(APOLLO)
    assert len(m.auth) == 1
    inj = m.auth[0]
    assert inj.provider == "apollo"
    assert inj.where == "header"
    assert inj.name == "X-Api-Key"
    assert inj.value_template == "{{ credentials.apollo }}"
    assert m.declared_providers == ("apollo",)


@pytest.mark.unit
def test_parses_request_block() -> None:
    m = parse_manifest(APOLLO)
    assert m.request.method == "POST"
    assert m.request.url_template == "https://api.apollo.io/v1/people/match"
    assert m.request.json_body == {"email": "{{ inputs.email }}"}
    assert m.request.pagination_type == "none"


@pytest.mark.unit
def test_parses_outputs_with_from_templates() -> None:
    m = parse_manifest(APOLLO)
    by_name = {o.name: o.from_template for o in m.outputs}
    assert by_name == {
        "full_name": "{{ response.person.name }}",
        "title": "{{ response.person.title }}",
    }


@pytest.mark.unit
def test_parses_error_rules_with_status_lists() -> None:
    m = parse_manifest(APOLLO)
    rules = {tuple(sorted(r.statuses)): r for r in m.errors}
    assert rules[(429,)].error_class is ErrorClass.RATE_LIMITED
    assert rules[(429,)].retryable is True
    # provider_error omits `retryable` → defaults to the taxonomy's retryable set.
    assert rules[(500, 502, 503)].error_class is ErrorClass.PROVIDER_ERROR
    assert rules[(500, 502, 503)].retryable is True
    # auth_error is terminal.
    assert rules[(401,)].retryable is False


@pytest.mark.unit
def test_parses_cost_model() -> None:
    m = parse_manifest(APOLLO)
    assert m.cost_model.unit == "credit"
    assert m.cost_model.per_call == 1


# ── Rejections ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_rejects_unsupported_manifest_schema() -> None:
    bad = {**APOLLO, "manifest_schema": "2"}
    with pytest.raises(ManifestError, match="manifest_schema"):
        parse_manifest(bad)


@pytest.mark.unit
def test_rejects_missing_id() -> None:
    bad = {k: v for k, v in APOLLO.items() if k != "id"}
    with pytest.raises(ManifestError, match="`id`"):
        parse_manifest(bad)


@pytest.mark.unit
def test_rejects_non_http_manifest_kind() -> None:
    with pytest.raises(ManifestError, match="http_manifest"):
        parse_manifest({**APOLLO, "kind": "code"})


@pytest.mark.unit
def test_rejects_missing_request_block() -> None:
    bad = {k: v for k, v in APOLLO.items() if k != "request"}
    with pytest.raises(ManifestError, match="request"):
        parse_manifest(bad)


@pytest.mark.unit
def test_rejects_bad_http_method() -> None:
    bad = {**APOLLO, "request": {**APOLLO["request"], "method": "FETCH"}}
    with pytest.raises(ManifestError, match="method"):
        parse_manifest(bad)


@pytest.mark.unit
def test_rejects_unsupported_pagination() -> None:
    bad = {**APOLLO, "request": {**APOLLO["request"], "pagination": {"type": "cursor"}}}
    with pytest.raises(ManifestError, match="pagination"):
        parse_manifest(bad)


@pytest.mark.unit
def test_rejects_invalid_error_class() -> None:
    bad = {**APOLLO, "errors": [{"when": {"status": 418}, "then": {"class": "teapot"}}]}
    with pytest.raises(ManifestError, match="error class"):
        parse_manifest(bad)


@pytest.mark.unit
def test_rejects_bad_inject_type() -> None:
    bad = {
        **APOLLO,
        "auth": [{"provider": "apollo", "inject": {"type": "cookie", "name": "x"}}],
    }
    with pytest.raises(ManifestError, match=r"inject\.type"):
        parse_manifest(bad)
