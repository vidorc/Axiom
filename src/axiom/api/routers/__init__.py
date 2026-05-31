"""API routers — one module per domain surface, mounted by the app factory.

Each router is a thin HTTP adapter over a domain store/engine: it maps requests
onto domain calls and domain records onto response schemas, and translates domain
exceptions into HTTP status codes. No business logic lives here.
"""

from __future__ import annotations

from axiom.api.routers import credentials, executions, stream, workflows

__all__ = ["credentials", "executions", "stream", "workflows"]
