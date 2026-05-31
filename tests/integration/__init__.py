"""Integration test lane — requires Postgres + Redis (service-container lane).

Run with `pytest -m integration` (or `make test-integration`). CI provisions
Postgres and Redis as service containers for this lane. Phase 0 keeps it nearly
empty; the readiness-probe round-trip and the first DB-backed tests land here as
Phase 1 adds real persistence.
"""

from __future__ import annotations
