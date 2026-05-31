"""FastAPI application factory.

Mounts the domain routers (workflows, executions, live stream) under a versioned
`/v1` prefix on top of the health/readiness probes. Process-lifetime resources —
the durable engine (Postgres store + node runtime + authoring), Redis — are built
once in the lifespan and shared via `app.state`, so the AuthoringStore's
immutable-version graph cache stays warm across requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from axiom import __version__
from axiom.api.routers import credentials, executions, stream, workflows
from axiom.composition import build_durable_engine
from axiom.platform.config import get_settings
from axiom.platform.db import dispose_engine, get_session_factory
from axiom.platform.logging import configure_logging, get_logger
from axiom.platform.redis import close_redis, get_redis

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage process-lifetime resources (graceful startup/shutdown)."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    # The durable engine is the API's gateway to the domains: it enqueues runs,
    # reads run/event state, and authors workflows. Built once, shared by every
    # request via app.state (and by the websocket stream).
    app.state.durable = build_durable_engine(get_session_factory())
    log.info("api.startup", environment=settings.environment, version=__version__)
    yield
    await close_redis()
    await dispose_engine()
    log.info("api.shutdown")


def create_app() -> FastAPI:
    """Build the FastAPI app. Importable by tests without starting a server."""
    app = FastAPI(
        title="Axiom API",
        version=__version__,
        summary="The orchestration layer for GTM engineers.",
        lifespan=lifespan,
    )

    # CORS: the web app is served from a different origin than the API, so a
    # browser blocks every request without an explicit allowlist. Origins come
    # from settings (localhost dev ports by default; the real frontend origin via
    # AXIOM_API_CORS_ALLOW_ORIGINS in prod) — never a wildcard, so this stays
    # correct once credentialed auth lands (WS-4).
    cors_settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_settings.api_cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        """Liveness probe — the process is up. No dependency checks."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["system"])
    async def ready(response: Response) -> dict[str, Any]:
        """Readiness probe — Postgres and Redis are reachable.

        Used by docker compose healthchecks and (later) k8s. Returns 200 only
        when *both* dependencies respond; 503 otherwise. The status *code* (not
        just the body) carries the verdict, because orchestrators key their
        rotation decisions off the code — a degraded pod must fall out of the
        load balancer, which only happens on a non-2xx.
        """
        checks: dict[str, str] = {}
        # Redis round-trip.
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc!s}"
        # Postgres round-trip.
        try:
            from sqlalchemy import text

            from axiom.platform.db import get_session_factory

            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as exc:
            checks["postgres"] = f"error: {exc!s}"

        healthy = all(v == "ok" for v in checks.values())
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if healthy else "degraded", "checks": checks}

    # Domain surfaces, mounted on top of the system probes.
    app.include_router(workflows.router)
    app.include_router(executions.router)
    app.include_router(credentials.router)
    app.include_router(stream.router)

    return app
