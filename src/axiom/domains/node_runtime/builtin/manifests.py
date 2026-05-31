"""First-party example ``http_manifest`` nodes (PHASE_1.md §5, SDK_SPEC.md §3).

These are real, declarative provider nodes — no Python — that ship in the default
registry as worked examples and as the actual nodes a cold-outbound workflow uses.
They prove the manifest path end to end: a contributor adds a provider by writing
one of these, not a program (SDK_SPEC.md §2).

Each manifest is the *contract* the node author writes; the runtime
(:func:`build_manifest_node`) turns it into an executable node. The request shapes
follow each provider's public API; a node author copies one of these to add the
next provider.
"""

from __future__ import annotations

from typing import Any

# axiom/apollo-enrich — the canonical enrichment node (SDK_SPEC.md §3.1). Enrich a
# person by email via Apollo's people/match endpoint, with the API key injected
# from the vault as a header that never touches a log.
APOLLO_ENRICH: dict[str, Any] = {
    "id": "axiom/apollo-enrich",
    "version": "1.0.0",
    "kind": "http_manifest",
    "manifest_schema": "1",
    "category": "enrichment",
    "display": {
        "label": "Apollo — Enrich Person",
        "summary": "Enrich a person by email via Apollo.",
        "icon": "apollo",
    },
    "auth": [
        {
            "provider": "apollo",
            "inject": {"type": "header", "name": "X-Api-Key", "value": "{{ credentials.apollo }}"},
        }
    ],
    "inputs": {
        "type": "object",
        "required": ["email"],
        "properties": {
            "email": {"type": "string", "format": "email", "title": "Person email"},
            "reveal_phone": {"type": "boolean", "default": False, "title": "Reveal phone"},
        },
    },
    "request": {
        "method": "POST",
        "url": "https://api.apollo.io/v1/people/match",
        "body": {
            "json": {
                "email": "{{ inputs.email }}",
                "reveal_phone_number": "{{ inputs.reveal_phone }}",
            }
        },
        "pagination": {"type": "none"},
    },
    "outputs": {
        "type": "object",
        "properties": {
            "full_name": {"type": "string", "from": "{{ response.person.name }}"},
            "title": {"type": "string", "from": "{{ response.person.title }}"},
            "company": {"type": "string", "from": "{{ response.person.organization.name }}"},
            "linkedin": {"type": "string", "from": "{{ response.person.linkedin_url }}"},
        },
    },
    "errors": [
        {"when": {"status": 429}, "then": {"class": "rate_limited", "retryable": True}},
        {
            "when": {"status": [500, 502, 503]},
            "then": {"class": "provider_error", "retryable": True},
        },
        {"when": {"status": 422}, "then": {"class": "invalid_input", "retryable": False}},
        {"when": {"status": 401}, "then": {"class": "auth_error", "retryable": False}},
    ],
    "rate_limit_hint": {"rpm": 600, "concurrency": 5},
    "cost_model": {"unit": "credit", "per_call": 1},
}

# axiom/smartlead-add-lead — the outreach side-effect terminus (PHASE_1.md §5,
# node 7). Adds a lead to a Smartlead campaign. The api_key goes in the query per
# Smartlead's API; the campaign id and lead fields come from inputs.
SMARTLEAD_ADD_LEAD: dict[str, Any] = {
    "id": "axiom/smartlead-add-lead",
    "version": "1.0.0",
    "kind": "http_manifest",
    "manifest_schema": "1",
    "category": "outreach",
    "display": {
        "label": "Smartlead — Add Lead to Campaign",
        "summary": "Add a lead to a Smartlead campaign.",
        "icon": "smartlead",
    },
    "auth": [
        {
            "provider": "smartlead",
            "inject": {"type": "query", "name": "api_key", "value": "{{ credentials.smartlead }}"},
        }
    ],
    "inputs": {
        "type": "object",
        "required": ["campaign_id", "email"],
        "properties": {
            "campaign_id": {"type": "string", "title": "Campaign ID"},
            "email": {"type": "string", "format": "email", "title": "Lead email"},
            "first_name": {"type": "string", "title": "First name"},
            "company_name": {"type": "string", "title": "Company"},
        },
    },
    "request": {
        "method": "POST",
        "url": "https://server.smartlead.ai/api/v1/campaigns/{{ inputs.campaign_id }}/leads",
        "body": {
            "json": {
                "lead_list": [
                    {
                        "email": "{{ inputs.email }}",
                        "first_name": "{{ inputs.first_name }}",
                        "company_name": "{{ inputs.company_name }}",
                    }
                ]
            }
        },
        "pagination": {"type": "none"},
    },
    "outputs": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "from": "{{ response.ok }}"},
            "upload_count": {"type": "integer", "from": "{{ response.upload_count }}"},
        },
    },
    "errors": [
        {"when": {"status": 429}, "then": {"class": "rate_limited", "retryable": True}},
        {
            "when": {"status": [500, 502, 503]},
            "then": {"class": "provider_error", "retryable": True},
        },
        {"when": {"status": 422}, "then": {"class": "invalid_input", "retryable": False}},
        {"when": {"status": 401}, "then": {"class": "auth_error", "retryable": False}},
    ],
    "rate_limit_hint": {"rpm": 60, "concurrency": 2},
    "cost_model": {"unit": "call", "per_call": 0},
}

# Every example manifest the platform ships, in one place.
EXAMPLE_MANIFESTS: tuple[dict[str, Any], ...] = (APOLLO_ENRICH, SMARTLEAD_ADD_LEAD)
