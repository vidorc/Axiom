"""Integration test: the readiness probe against live Postgres + Redis.

The first real integration test and the proof that the platform wiring works
end to end — config → engine → session and config → redis client. Requires the
service containers (CI) or `make up` (local). Run with `pytest -m integration`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from axiom.api.app import create_app


@pytest.mark.integration
def test_ready_probe_reports_dependencies_healthy(pg_engine: AsyncEngine) -> None:
    # Depend on ``pg_engine`` so the lane skips cleanly (rather than failing on a
    # 503) when the test datastore is down — matching every sibling integration
    # test. The probe checks Postgres + Redis; this guard proves Postgres is up.
    with TestClient(create_app()) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    # Both dependencies must round-trip for the stack to be considered ready.
    assert body["checks"].get("postgres") == "ok", body
    assert body["checks"].get("redis") == "ok", body
    assert body["status"] == "ready", body
