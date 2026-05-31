"""Smoke test for the API app factory.

Proves the FastAPI app builds and the liveness probe responds without any
external services — so `docker compose up` and CI have a real signal that the
api run mode boots. The readiness probe (which checks Postgres/Redis) is
exercised in the integration lane.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from axiom import __version__
from axiom.api.app import create_app


@pytest.mark.unit
def test_app_builds() -> None:
    app = create_app()
    assert app.title == "Axiom API"
    assert app.version == __version__


@pytest.mark.unit
def test_health_probe_returns_ok() -> None:
    # TestClient drives lifespan startup/shutdown; no DB/Redis needed for /health.
    with TestClient(create_app()) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


@pytest.mark.unit
def test_cors_allows_configured_browser_origin() -> None:
    """A cross-origin browser request from an allowed origin is permitted.

    Regression guard: the web app is served from a different origin than the API,
    so without CORS middleware a browser blocks every builder save/run call. This
    asserts a preflight from a configured dev origin gets the
    ``Access-Control-Allow-Origin`` header back. (Discovered via the Playwright
    E2E: server-side calls don't enforce CORS, so only a real browser surfaced it.)
    """
    origin = "http://localhost:3010"
    with TestClient(create_app()) as client:
        resp = client.options(
            "/v1/workflows",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert resp.status_code in (200, 204), resp.text
    assert resp.headers.get("access-control-allow-origin") == origin


@pytest.mark.unit
def test_ready_probe_returns_503_when_dependencies_down() -> None:
    """Readiness must fail with a non-2xx when a dependency is unreachable.

    Regression guard: the probe previously returned 200 with a "degraded" body,
    so orchestrator healthchecks (which key off the status *code*) could never
    pull a broken pod from rotation. With no test datastore configured here, both
    Redis and Postgres round-trips fail → 503.
    """
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503, resp.text
    assert resp.json()["status"] == "degraded"


@pytest.mark.unit
def test_cors_omits_unlisted_origin() -> None:
    """An origin not in the allowlist gets no ACAO header (no wildcard leak)."""
    with TestClient(create_app()) as client:
        resp = client.options(
            "/v1/workflows",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
    # Either no header, or not echoing the disallowed origin — never "*".
    acao = resp.headers.get("access-control-allow-origin")
    assert acao != "https://evil.example.com"
    assert acao != "*"
