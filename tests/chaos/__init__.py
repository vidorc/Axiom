"""Chaos test lane — crash recovery & idempotency under failure (PHASE_1.md §6).

> This is the single most important test in the codebase (ADR-0001).

Run with `pytest -m chaos`. CI runs this in its own lane with Postgres + Redis.

The test that lands here in Phase 1: start a multi-node DAG, `kill -9` a worker
mid-execution, and assert the run resumes to **exactly-once** completion — no
lost run, no double side-effect (idempotency key (run_id, node_id, attempt)).
Phase 0 Spike A proves this is achievable with throwaway code; Phase 1 promotes
it to this automated CI gate so recovery/idempotency can never silently regress.

Intentionally empty until the orchestrator exists (PHASE_1.md WS-1). Reserving the
lane now so there is an obvious, named home for the gate — not an afterthought.
"""

from __future__ import annotations
