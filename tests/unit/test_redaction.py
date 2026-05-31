"""Tests for the secret-redaction layer (SECURITY.md §5.4).

This is the most important test in the Phase-0 skeleton. The single most common
real-world secret leak is logging a request with an Authorization header. These
tests assert that a known secret value never survives the redaction processor —
the structural guarantee behind "never expose user credentials" (BLUEPRINT.md
principle 6). PHASE_1.md §6 extends this to the instrumented ctx.http client.
"""

from __future__ import annotations

import pytest

from axiom.platform.logging import _MASK, redaction_processor

SECRET = "sk-live-supersecret-DO-NOT-LEAK"


@pytest.mark.unit
def test_top_level_sensitive_key_is_masked() -> None:
    event = {"event": "http.request", "api_key": SECRET}
    out = redaction_processor(None, "info", event)
    assert out["api_key"] == _MASK
    assert SECRET not in str(out)


@pytest.mark.unit
def test_authorization_header_is_masked() -> None:
    event = {"event": "outbound", "headers": {"Authorization": f"Bearer {SECRET}"}}
    out = redaction_processor(None, "info", event)
    assert out["headers"]["Authorization"] == _MASK
    assert SECRET not in str(out)


@pytest.mark.unit
def test_nested_and_listed_secrets_are_masked() -> None:
    event = {
        "creds": [{"token": SECRET}, {"password": SECRET}],
        "nested": {"deep": {"client_secret": SECRET}},
    }
    out = redaction_processor(None, "info", event)
    assert SECRET not in str(out), "a secret survived redaction in a nested structure"


@pytest.mark.unit
def test_case_insensitive_key_matching() -> None:
    event = {"API_KEY": SECRET, "X-Api-Key": SECRET, "ToKeN": SECRET}
    out = redaction_processor(None, "info", event)
    assert SECRET not in str(out)


@pytest.mark.unit
def test_non_sensitive_values_pass_through() -> None:
    event = {"event": "run.started", "run_id": "abc123", "org_id": "org_1"}
    out = redaction_processor(None, "info", event)
    assert out["run_id"] == "abc123"
    assert out["org_id"] == "org_1"
